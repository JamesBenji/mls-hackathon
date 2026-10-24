"""Model gateway: thin async wrapper over the Google Generative Language API
(the hosted Gemma models). No clinical logic in here.

CONTRACT (other modules rely on it):

    class ModelError(Exception)                 base
    class ModelUnavailable(ModelError)          connection error, timeout, HTTP 5xx
                                                (after bounded retries), auth
                                                failure, rate limit, model not
                                                found (404) -> callers fall back
    class ModelOutputInvalid(ModelError)        JSON / Pydantic validation still
                                                failing after the single retry
                                                (.errors / .raw_output for debugging)

    set_transport(transport | None)             tests inject httpx.MockTransport

    async chat(model, messages, *, schema=None, temperature=0.2,
               timeout=None, images_b64=None, num_predict=None) -> str
        POST /models/{model}:generateContent. OpenAI-style messages
        (role: system|user|assistant, content: str) are converted: Gemma has
        no system role on this API, so system messages are folded into the
        first user turn as a prefix. `timeout` (default MODEL_TIMEOUT_S) is a
        hard deadline for the HTTP request; waiting for a free generation
        slot is not counted.
    async chat_json(model, messages, schema_model, **kw) -> BaseModel
        temperature 0 unless kw overrides it, a surrounding ```json fence is
        tolerated. Gemma does not support schema-constrained output on this
        API, so the schema is stated in the prompt; on a ValidationError ONE
        retry with the invalid reply + the validation errors appended; still
        invalid -> ModelOutputInvalid. ModelUnavailable propagates unchanged.
    async list_models() -> list[str]             GET /models  ("models/" prefix stripped)
    async probe_models() -> dict                 for GET /health/models (never raises)
    async health() -> dict                       cheap liveness for probes (never raises)

All generations are gated by an asyncio.Semaphore sized
settings.model_max_concurrent (cloud rate-limit safety). One semaphore per
running event loop, because an asyncio.Semaphore bound to one loop raises
when used from another.

The API key is sent as the x-goog-api-key header (never in the URL, so it
cannot leak into logs). trust_env=False: never route patient data through
an HTTP(S)_PROXY from the environment. Settings are read at call time
(tests monkeypatch the singleton). Exception messages and logs never
contain prompts or model output.

The hosted Gemma models answer 500 "Internal error encountered"
intermittently under load (observed 2026-10-24: an identical payload
succeeded 2 of 3 times, and the failures returned in under a second), so
5xx responses are retried up to _5XX_RETRIES times with a short backoff,
inside the caller's timeout budget. 4xx are deterministic (bad key,
unknown model, bad request body) and fail immediately.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
import weakref
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import settings

log = logging.getLogger(__name__)

META_TIMEOUT_S = 5.0  # GET /models
PROBE_TIMEOUT_S = 20.0  # latency probe for /health/models
PROBE_NUM_PREDICT = 8
_5XX_RETRIES = 2  # extra attempts after a 5xx response before giving up
_RETRY_BACKOFF_S = 1.0  # pause between 5xx attempts (tests zero this)
_PROBE_MESSAGES = [{"role": "user", "content": "Reply with the single word: ok"}]
_DETAIL_MAX = 200  # chars of an API error body kept in exception messages
_RETRY_ECHO_MAX = 8000  # chars of an invalid reply echoed back on the retry
_MAX_LISTED_ERRORS = 12

M = TypeVar("M", bound=BaseModel)


class ModelError(Exception):
    """Base class for model gateway errors."""


class ModelUnavailable(ModelError):
    """API unreachable, timed out, 5xx, auth failed, rate limited, or the
    model name does not exist."""


class ModelOutputInvalid(ModelError):
    """Structured output failed validation even after one retry.

    `errors` ("field: message" lines) and `raw_output` (the last invalid
    reply) are kept for debugging; the reply is not part of str(exc).
    """

    def __init__(self, message: str, *, errors: list[str] | None = None,
                 raw_output: str | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []
        self.raw_output = raw_output


_transport: httpx.AsyncBaseTransport | None = None

# running event loop -> (limit it was created with, semaphore)
_gates: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[int, asyncio.Semaphore]] = (
    weakref.WeakKeyDictionary()
)
_gates_lock = threading.Lock()


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Route every request through `transport` (tests: httpx.MockTransport).
    None restores the real network transport."""
    global _transport
    _transport = transport


# --- HTTP plumbing -------------------------------------------------------------

def _base_url() -> str:
    return (settings.gemma_base_url or "").strip().rstrip("/")


def _clip(text: str, limit: int = _DETAIL_MAX) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _exc_text(exc: BaseException) -> str:
    detail = _clip(str(exc))
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def _describe(exc: BaseException) -> str:
    """Our own errors are already worded for humans; others get their type."""
    return str(exc) if isinstance(exc, ModelError) else _exc_text(exc)


def _error_detail(resp: httpx.Response) -> str:
    """The API's {"error": {"message": ...}} text (clipped). Never the request body."""
    try:
        body = resp.json()
    except ValueError:
        return _clip(resp.text) or "(empty body)"
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return _clip(str(err["message"]))
        if err:
            return _clip(str(err))
    return _clip(resp.text)


def _headers() -> dict[str, str]:
    key = settings.gemma_api_key or ""
    return {"x-goog-api-key": key} if key else {}


async def _request(method: str, path: str, *, timeout: float,
                   payload: dict | None = None, model: str | None = None) -> dict:
    """One logical request to the API with the error mapping applied.

    `timeout` is also enforced as an asyncio deadline: httpx timeouts are
    per read, and injected transports (tests) ignore them entirely. The
    deadline spans all 5xx retries, so a caller's budget is never exceeded.
    """
    what = f"{method} {path}" + (f" model={model!r}" if model else "")
    try:
        async with asyncio.timeout(timeout):
            # trust_env=False: never route patient data through a proxy from
            # the environment; the key rides in a header, not the URL.
            async with httpx.AsyncClient(base_url=_base_url(), transport=_transport,
                                         timeout=timeout, trust_env=False) as client:
                resp = await client.request(method, path, json=payload,
                                            headers=_headers())
                for retry in range(1, _5XX_RETRIES + 1):
                    if resp.status_code < 500:
                        break
                    log.warning("gemma api %s: HTTP %d, retrying (%d/%d)",
                                what, resp.status_code, retry, _5XX_RETRIES)
                    await asyncio.sleep(_RETRY_BACKOFF_S)
                    resp = await client.request(method, path, json=payload,
                                                headers=_headers())
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise ModelUnavailable(f"Gemma API timed out after {timeout:g}s ({what})") from exc
    except (httpx.TransportError, httpx.InvalidURL) as exc:
        raise ModelUnavailable(f"Gemma API unreachable ({what}): {_exc_text(exc)}") from exc
    except httpx.HTTPError as exc:  # decoding errors and the like
        raise ModelError(f"Gemma API request failed ({what}): {_exc_text(exc)}") from exc

    status = resp.status_code
    detail = _error_detail(resp)
    if status == 404:
        raise ModelUnavailable(
            f"model {model!r} not found on the Gemma API (HTTP 404; fix the "
            f"configured name): {detail}" if model
            else f"Gemma API endpoint not found ({what}, HTTP 404)")
    if status in (401, 403):
        raise ModelUnavailable(
            f"Gemma API rejected the API key (HTTP {status}; check GOOGLE_API_KEY): {detail}")
    if status == 429:
        raise ModelUnavailable(f"Gemma API rate limit (HTTP 429, {what}): {detail}")
    if status >= 500:
        raise ModelUnavailable(f"Gemma API HTTP {status} ({what}): {detail}")
    if not resp.is_success:
        # 400 with "API key not valid" is a config problem, not a bad request.
        if status == 400 and "api key not valid" in detail.lower():
            raise ModelUnavailable(f"Gemma API key not valid (HTTP 400): {detail}")
        raise ModelError(f"Gemma API HTTP {status} ({what}): {detail}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise ModelError(f"Gemma API returned a non-JSON body ({what})") from exc
    if not isinstance(body, dict):
        raise ModelError(f"Gemma API returned an unexpected JSON body ({what})")
    return body


def _gate() -> asyncio.Semaphore:
    """The generation semaphore for the running loop, created lazily and
    re-created if settings.model_max_concurrent changed."""
    loop = asyncio.get_running_loop()
    limit = max(1, int(settings.model_max_concurrent))
    with _gates_lock:
        entry = _gates.get(loop)
        if entry is None or entry[0] != limit:
            entry = (limit, asyncio.Semaphore(limit))
            _gates[loop] = entry
        return entry[1]


# --- message conversion --------------------------------------------------------

def _strip_data_url(image: str) -> tuple[str, str]:
    """(mime_type, bare base64) from a possible "data:image/png;base64,..." URL."""
    if image.startswith("data:") and "," in image:
        meta, data = image.split(",", 1)
        mime = meta[5:].split(";", 1)[0] or "image/jpeg"
        return mime, data
    return "image/jpeg", image


def _image_parts(images: list[str]) -> list[dict]:
    return [
        {"inline_data": {"mime_type": mime, "data": data}}
        for mime, data in (_strip_data_url(i) for i in images)
    ]


def _contents(messages: list[dict], images_b64: list[str] | None) -> list[dict]:
    """OpenAI-style messages -> Gemini `contents`. System messages are folded
    into the first user turn (Gemma has no system role on this API); a
    message's own "images" key and the images_b64 argument (attached to the
    LAST user turn) become inline_data parts. Never mutates the input."""
    system_prefix = "\n\n".join(
        str(m.get("content", "")).strip()
        for m in messages if m.get("role") == "system" and m.get("content")
    ).strip()

    contents: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        if role == "system":
            continue
        gemini_role = "model" if role in ("assistant", "model") else "user"
        parts: list[dict] = [{"text": str(msg.get("content", ""))}]
        own_images = msg.get("images")
        if isinstance(own_images, list) and own_images:
            parts.extend(_image_parts(own_images))
        if contents and contents[-1]["role"] == gemini_role:
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": gemini_role, "parts": parts})

    if system_prefix:
        for content in contents:
            if content["role"] == "user":
                content["parts"][0] = {
                    "text": f"{system_prefix}\n\n{content['parts'][0].get('text', '')}"
                }
                break
        else:
            contents.insert(0, {"role": "user", "parts": [{"text": system_prefix}]})

    if images_b64:
        for content in reversed(contents):
            if content["role"] == "user":
                content["parts"].extend(_image_parts(images_b64))
                break
        else:
            contents.append({"role": "user", "parts": _image_parts(images_b64)})
    return contents


def _json_instruction(schema: dict) -> str:
    return (
        "\n\nReply with ONLY a single valid JSON object that is an instance of "
        "(not a description of) this JSON schema. No prose, no markdown, no "
        "code fences:\n" + json.dumps(schema, separators=(",", ":"))
    )


def _generation_config(*, temperature: float, num_predict: int | None) -> dict[str, Any]:
    config: dict[str, Any] = {"temperature": temperature}
    if num_predict is not None:
        config["maxOutputTokens"] = num_predict
    return config


def _chat_payload(model: str, messages: list[dict], *, schema: dict | None,
                  temperature: float, images_b64: list[str] | None,
                  num_predict: int | None) -> dict[str, Any]:
    contents = _contents(messages, images_b64)
    if schema is not None:
        # Gemma on this API has no grammar-constrained output; state the
        # schema in the prompt instead.
        for content in reversed(contents):
            if content["role"] == "user":
                content["parts"][0] = {
                    "text": str(content["parts"][0].get("text", "")) + _json_instruction(schema)
                }
                break
    return {
        "contents": contents,
        "generationConfig": _generation_config(temperature=temperature,
                                                num_predict=num_predict),
    }


def _reply_text(model: str, body: dict) -> str:
    candidates = body.get("candidates")
    candidate = candidates[0] if isinstance(candidates, list) and candidates else None
    content = candidate.get("content") if isinstance(candidate, dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None
    text = "".join(
        str(p.get("text", "")) for p in parts if isinstance(p, dict)
    ) if isinstance(parts, list) else ""
    if not text.strip():
        finish = candidate.get("finishReason") if isinstance(candidate, dict) else None
        reason = f" (finishReason={finish})" if finish else ""
        raise ModelError(f"Gemma API reply from {model!r} has no text{reason}")
    return text


# --- chat ----------------------------------------------------------------------

async def _generate(model: str, payload: dict, timeout: float) -> tuple[dict, float]:
    """Wait for a generation slot, then one generateContent round trip.
    Returns (body, seconds spent in the request itself)."""
    async with _gate():
        started = time.perf_counter()
        path = f"/models/{model}:generateContent"
        body = await _request("POST", path, timeout=timeout, payload=payload, model=model)
        return body, time.perf_counter() - started


async def chat(model: str, messages: list[dict], *, schema: dict | None = None,
               temperature: float = 0.2, timeout: float | None = None,
               images_b64: list[str] | None = None, num_predict: int | None = None) -> str:
    """One non-streaming generation; returns the reply text.

    schema: JSON Schema (Pydantic .model_json_schema()); the API cannot
    constrain Gemma's output, so the schema is appended to the prompt and the
    reply is validated by the caller (chat_json). Raises ModelUnavailable
    (unreachable, timeout, auth, rate limit, 404 unknown model, 5xx: callers
    fall back) or ModelError (other 4xx, blocked or malformed reply).
    """
    payload = _chat_payload(model, messages, schema=schema, temperature=temperature,
                            images_b64=images_b64, num_predict=num_predict)
    body, elapsed = await _generate(model, payload, timeout or settings.model_timeout_s)
    text = _reply_text(model, body)
    candidates = body.get("candidates")
    finish = (candidates[0].get("finishReason")
              if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict)
              else None)
    log.debug("gemma chat model=%s %.0f ms finish=%s", model, elapsed * 1000, finish)
    if finish == "MAX_TOKENS" and num_predict is None:
        log.warning("gemma chat model=%s stopped at the token limit (MAX_TOKENS); "
                    "output is probably truncated", model)
    if finish in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST"):
        raise ModelError(f"Gemma API blocked the reply from {model!r} (finishReason={finish})")
    return text


# --- structured output ---------------------------------------------------------

_FENCE = re.compile(r"^\s*```[\w-]*[ \t]*\r?\n?(.*?)\s*```\s*$", re.DOTALL)


def _strip_fence(text: str) -> str:
    """Drop a ```json ... ``` fence that surrounds the whole reply."""
    match = _FENCE.match(text)
    return match.group(1) if match else text


def _extract_json(text: str) -> str:
    """The JSON payload of a reply. Gemma cannot be grammar-constrained on
    this API, so the object may arrive fenced or wrapped in prose: take a
    whole-reply fence, else the first balanced {...} (string-aware), else
    the raw text (the caller's validation error names the problem)."""
    fenced = _strip_fence(text)
    if fenced is not text:
        return fenced
    start = fenced.find("{")
    if start < 0:
        return text
    depth = 0
    in_string = False
    escaped = False
    for i, ch in enumerate(fenced[start:], start):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return fenced[start:i + 1]
    return text


def _validation_problems(exc: ValidationError) -> list[str]:
    """Compact "field: message" lines. Input values are left out so model
    output (and the patient text it may echo) never lands in logs/messages."""
    errors = exc.errors(include_url=False, include_input=False)
    lines = []
    for err in errors[:_MAX_LISTED_ERRORS]:
        loc = ".".join(str(part) for part in err.get("loc", ())) or "(root)"
        lines.append(f"{loc}: {err.get('msg', 'invalid value')}")
    if len(errors) > _MAX_LISTED_ERRORS:
        lines.append(f"... and {len(errors) - _MAX_LISTED_ERRORS} more")
    return lines


def _retry_instruction(problems: list[str], schema: dict) -> str:
    return (
        "Your previous reply did not match the required JSON schema. Validation errors:\n"
        + "\n".join(f"- {p}" for p in problems)
        + "\n\nReply again with ONLY valid JSON matching the schema below (an instance "
        "of the schema, not the schema itself). No prose, no markdown, no code fences.\n"
        + json.dumps(schema, separators=(",", ":"))
    )


async def chat_json(model: str, messages: list[dict], schema_model: type[M], **kw: Any) -> M:
    """JSON generation validated by `schema_model`.

    kw is passed to chat() (timeout, temperature, images_b64, num_predict).
    Retries ONCE with the validation errors appended to the conversation,
    then raises ModelOutputInvalid. ModelUnavailable / ModelError from the
    transport propagate unchanged so callers can fall back.
    """
    if "schema" in kw:
        raise TypeError("chat_json() derives the schema from schema_model; do not pass schema=")
    schema = schema_model.model_json_schema()
    kw.setdefault("temperature", 0)
    # Attach images once, to the caller's last user message, so they stay
    # there in the retry conversation as well.
    conversation = messages
    images_b64 = kw.pop("images_b64", None)
    if images_b64:
        conversation = _contents_with_images(messages, images_b64)
    name = schema_model.__name__

    reply = await chat(model, conversation, schema=schema, **kw)
    try:
        return schema_model.model_validate_json(_extract_json(reply))
    except ValidationError as exc:
        problems = _validation_problems(exc)
    log.warning("%s from %s failed validation, retrying once: %s",
                name, model, "; ".join(problems))

    if len(reply) > _RETRY_ECHO_MAX:
        reply = reply[:_RETRY_ECHO_MAX] + " ...[truncated]"
    retry = [
        *conversation,
        {"role": "assistant", "content": reply},
        {"role": "user", "content": _retry_instruction(problems, schema)},
    ]
    reply = await chat(model, retry, schema=schema, **kw)
    try:
        return schema_model.model_validate_json(_extract_json(reply))
    except ValidationError as exc:
        problems = _validation_problems(exc)
        log.warning("%s from %s still invalid after one retry: %s",
                    name, model, "; ".join(problems))
        raise ModelOutputInvalid(
            f"{name} output from {model!r} still invalid after one retry: "
            + "; ".join(problems),
            errors=problems, raw_output=reply,
        ) from exc


def _contents_with_images(messages: list[dict], images_b64: list[str]) -> list[dict]:
    """The caller's messages with a trailing user turn that carries the
    images, so the retry conversation keeps them (mirrors the old Ollama
    client's _attach_images)."""
    return [
        *messages,
        {"role": "user", "content": "", "images": images_b64},
    ]


# --- model inventory + health --------------------------------------------------

async def list_models(*, timeout: float | None = None) -> list[str]:
    """Model names the key may use (GET /models, "models/" prefix stripped)."""
    body = await _request("GET", "/models", timeout=timeout or META_TIMEOUT_S)
    names: list[str] = []
    for entry in body.get("models") or []:
        if isinstance(entry, dict):
            name = entry.get("name")
            if isinstance(name, str) and name:
                names.append(name.removeprefix("models/"))
    return names


async def _probe_latency_ms(model: str, timeout_s: float) -> float:
    """Round trip of a few-token chat. The deadline also bounds waiting for a
    free generation slot; the reported time excludes that wait."""
    payload = _chat_payload(model, _PROBE_MESSAGES, schema=None, temperature=0,
                            images_b64=None, num_predict=PROBE_NUM_PREDICT)
    async with asyncio.timeout(timeout_s):
        body, elapsed = await _generate(model, payload, timeout_s)
    _reply_text(model, body)
    return round(elapsed * 1000, 1)


async def probe_models(*, timeout_s: float = PROBE_TIMEOUT_S) -> dict:
    """Configured-model status for GET /health/models. Never raises.

    {"gemma": "up"|"down", "pipeline", "max_concurrent",
     "model", "present", "latency_ms", ["error"]}
    """
    model = (settings.gemma_model or "").strip()
    report: dict[str, Any] = {
        "gemma": "down",
        "pipeline": settings.clinical_pipeline,
        "max_concurrent": settings.model_max_concurrent,
        "model": model,
        "present": False,
        "latency_ms": None,
        "error": None,
    }
    if not settings.gemma_configured:
        report["error"] = "GOOGLE_API_KEY is not set"
        return report
    try:
        available = set(await list_models())
    except Exception as exc:  # noqa: BLE001 - never raises
        report["error"] = _describe(exc)
        return report
    report["gemma"] = "up"
    if not model:
        report["error"] = "no model configured (GEMMA_MODEL)"
        return report
    report["present"] = model in available
    if not report["present"]:
        report["error"] = f"model {model!r} is not available to this API key"
        return report
    try:
        report["latency_ms"] = await _probe_latency_ms(model, timeout_s)
    except TimeoutError:
        report["error"] = f"latency probe timed out after {timeout_s:g}s"
    except Exception as exc:  # noqa: BLE001 - never raises
        report["error"] = _describe(exc)
    return report


async def health() -> dict:
    """Cheap liveness (model list only, short timeout). Never raises."""
    if not settings.gemma_configured:
        return {"gemma": "down", "error": "GOOGLE_API_KEY is not set"}
    try:
        available = set(await list_models(timeout=META_TIMEOUT_S))
    except Exception as exc:  # noqa: BLE001 - liveness must never raise
        return {"gemma": "down", "error": _describe(exc)}
    return {
        "gemma": "up",
        "model": settings.gemma_model,
        "model_present": (settings.gemma_model or "") in available,
    }
