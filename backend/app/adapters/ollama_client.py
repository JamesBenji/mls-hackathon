"""Model gateway: thin async wrapper over Ollama's HTTP API.
No clinical logic in here (MEDGEMMA_DO_SETUP.md §5).

CONTRACT (implemented by the ollama-client task; other modules rely on it):

    class ModelError(Exception)                 base
    class ModelUnavailable(ModelError)          connection error, timeout, HTTP 5xx,
                                                model not found (404) -> callers fall back
    class ModelOutputInvalid(ModelError)        JSON / Pydantic validation still
                                                failing after the single retry
                                                (.errors / .raw_output for debugging)

    set_transport(transport | None)             tests inject httpx.MockTransport

    async chat(model, messages, *, schema=None, temperature=0.2,
               timeout=None, images_b64=None, num_predict=None) -> str
        POST /api/chat {model, messages, stream: false, keep_alive: "24h",
        options: {temperature, num_ctx: OLLAMA_NUM_CTX [, num_predict]},
        format: schema (only if given)} -> message.content.
        images_b64 go on (a copy of) the last user message; the caller's
        messages are never mutated. `timeout` (default MODEL_TIMEOUT_S) is a
        hard deadline for the HTTP request; waiting for a free generation
        slot is not counted (wrap in asyncio.wait_for for a total budget).
        Other 4xx, or a reply without message.content -> ModelError.
    async chat_json(model, messages, schema_model, **kw) -> BaseModel
        format = schema_model.model_json_schema(), temperature 0 unless kw
        overrides it, a surrounding ```json fence is tolerated. On a
        ValidationError (bad JSON included) ONE retry with the invalid reply +
        the validation errors appended to the conversation; still invalid ->
        ModelOutputInvalid. ModelUnavailable propagates unchanged.
    async list_tags() -> list[str]              GET /api/tags  model names
    async list_running() -> list[dict]          GET /api/ps    models entries
    async probe_models() -> dict                for GET /health/models (never raises)
    async health() -> dict                      cheap liveness for GET /health (never raises)
    placement(ps_entry) -> str | None           "100% GPU" | "100% CPU" | "NN% GPU / MM% CPU"

All generations are gated by an asyncio.Semaphore sized
settings.ollama_num_parallel (never more concurrent generations than
OLLAMA_NUM_PARALLEL). One semaphore per running event loop, because an
asyncio.Semaphore bound to one loop raises when used from another.

Settings are read at call time (tests monkeypatch the singleton). Exception
messages and logs never contain prompts or model output.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import threading
import time
import weakref
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import settings

log = logging.getLogger(__name__)

KEEP_ALIVE = "24h"  # keep models resident between calls (no cold loads mid-demo)
META_TIMEOUT_S = 5.0  # /api/tags, /api/ps, /api/version
PROBE_TIMEOUT_S = 20.0  # per-model latency probe for /health/models
PROBE_NUM_PREDICT = 5
_PROBE_MESSAGES = [{"role": "user", "content": "Reply with the single word: ok"}]
_DETAIL_MAX = 200  # chars of an Ollama error body kept in exception messages
_RETRY_ECHO_MAX = 8000  # chars of an invalid reply echoed back on the retry
_MAX_LISTED_ERRORS = 12

M = TypeVar("M", bound=BaseModel)


class ModelError(Exception):
    """Base class for model gateway errors."""


class ModelUnavailable(ModelError):
    """Ollama unreachable, timed out, 5xx, or the model tag does not exist."""


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
    url = (settings.ollama_url or "").strip()
    # Tolerate OLLAMA_HOST-style values such as "127.0.0.1:11434".
    if url and "://" not in url:
        url = f"http://{url}"
    return url


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
    """Ollama's {"error": "..."} text (clipped). Never the request body."""
    try:
        body = resp.json()
    except ValueError:
        return _clip(resp.text) or "(empty body)"
    if isinstance(body, dict) and body.get("error"):
        return _clip(str(body["error"]))
    return _clip(resp.text)


async def _request(method: str, path: str, *, timeout: float,
                   payload: dict | None = None, model: str | None = None) -> dict:
    """One HTTP round trip to Ollama with the error mapping applied.

    `timeout` is also enforced as an asyncio deadline: httpx timeouts are
    per read, and injected transports (tests) ignore them entirely.
    """
    what = f"{method} {path}" + (f" model={model!r}" if model else "")
    try:
        async with asyncio.timeout(timeout):
            # trust_env=False: Ollama is loopback / SSH tunnel only; never route
            # patient data through an HTTP(S)_PROXY from the environment.
            async with httpx.AsyncClient(base_url=_base_url(), transport=_transport,
                                         timeout=timeout, trust_env=False) as client:
                resp = await client.request(method, path, json=payload)
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise ModelUnavailable(f"Ollama timed out after {timeout:g}s ({what})") from exc
    except (httpx.TransportError, httpx.InvalidURL) as exc:
        raise ModelUnavailable(f"Ollama unreachable ({what}): {_exc_text(exc)}") from exc
    except httpx.HTTPError as exc:  # decoding errors and the like
        raise ModelError(f"Ollama request failed ({what}): {_exc_text(exc)}") from exc

    status = resp.status_code
    if status == 404:
        if model:
            raise ModelUnavailable(
                f"model {model!r} not found on Ollama (HTTP 404; pull the tag or fix "
                f"the configured name): {_error_detail(resp)}")
        raise ModelUnavailable(f"Ollama endpoint not found ({what}, HTTP 404)")
    if status >= 500:
        raise ModelUnavailable(f"Ollama HTTP {status} ({what}): {_error_detail(resp)}")
    if not resp.is_success:
        raise ModelError(f"Ollama HTTP {status} ({what}): {_error_detail(resp)}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise ModelError(f"Ollama returned a non-JSON body ({what})") from exc
    if not isinstance(body, dict):
        raise ModelError(f"Ollama returned an unexpected JSON body ({what})")
    return body


def _gate() -> asyncio.Semaphore:
    """The generation semaphore for the running loop, created lazily and
    re-created if settings.ollama_num_parallel changed."""
    loop = asyncio.get_running_loop()
    limit = max(1, int(settings.ollama_num_parallel))
    with _gates_lock:
        entry = _gates.get(loop)
        if entry is None or entry[0] != limit:
            entry = (limit, asyncio.Semaphore(limit))
            _gates[loop] = entry
        return entry[1]


# --- chat ----------------------------------------------------------------------

def _strip_data_url(image: str) -> str:
    # Ollama wants bare base64; tolerate "data:image/png;base64,..." URLs.
    if image.startswith("data:") and "," in image:
        return image.split(",", 1)[1]
    return image


def _attach_images(messages: list[dict], images_b64: list[str] | None) -> list[dict]:
    """Shallow copy of the conversation with images on the LAST user message
    (a user message is appended if there is none). Never mutates the input."""
    msgs = [dict(m) for m in messages]
    if not images_b64:
        return msgs
    images = [_strip_data_url(i) for i in images_b64]
    for msg in reversed(msgs):
        if msg.get("role") == "user":
            msg["images"] = [*(msg.get("images") or []), *images]
            break
    else:
        msgs.append({"role": "user", "content": "", "images": images})
    return msgs


def _chat_payload(model: str, messages: list[dict], *, schema: dict | None,
                  temperature: float, images_b64: list[str] | None,
                  num_predict: int | None) -> dict[str, Any]:
    # num_ctx is ALWAYS explicit: Ollama may default a model (Gemma 4) to a 4K
    # window and silently truncate long transcripts + history (§10 gotchas).
    options: dict[str, Any] = {"temperature": temperature, "num_ctx": settings.ollama_num_ctx}
    if num_predict is not None:
        options["num_predict"] = num_predict
    payload: dict[str, Any] = {
        "model": model,
        "messages": _attach_images(messages, images_b64),
        "stream": False,
        "keep_alive": KEEP_ALIVE,
        "options": options,
    }
    if schema is not None:
        payload["format"] = schema  # grammar-constrained output (§10)
    return payload


def _message_content(model: str, body: dict) -> str:
    message = body.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        err = body.get("error")
        raise ModelError(f"Ollama reply from {model!r} has no message.content"
                         + (f": {_clip(str(err))}" if err else ""))
    return content


async def _generate(model: str, payload: dict, timeout: float) -> tuple[dict, float]:
    """Wait for a generation slot, then one /api/chat round trip.
    Returns (body, seconds spent in the request itself)."""
    async with _gate():
        started = time.perf_counter()
        body = await _request("POST", "/api/chat", timeout=timeout, payload=payload, model=model)
        return body, time.perf_counter() - started


async def chat(model: str, messages: list[dict], *, schema: dict | None = None,
               temperature: float = 0.2, timeout: float | None = None,
               images_b64: list[str] | None = None, num_predict: int | None = None) -> str:
    """One non-streaming generation; returns message.content.

    schema: JSON Schema (Pydantic .model_json_schema()) sent as `format` so
    the output is constrained; otherwise free text. Raises ModelUnavailable
    (unreachable, timeout, 404 unknown model, 5xx: callers fall back) or
    ModelError (other 4xx, malformed reply).
    """
    payload = _chat_payload(model, messages, schema=schema, temperature=temperature,
                            images_b64=images_b64, num_predict=num_predict)
    body, elapsed = await _generate(model, payload, timeout or settings.model_timeout_s)
    content = _message_content(model, body)
    done_reason = body.get("done_reason")
    log.debug("ollama chat model=%s %.0f ms done_reason=%s eval_count=%s",
              model, elapsed * 1000, done_reason, body.get("eval_count"))
    if done_reason == "length" and num_predict is None:
        log.warning("ollama chat model=%s stopped at the token limit (done_reason=length); "
                    "output is probably truncated", model)
    return content


# --- structured output ---------------------------------------------------------

_FENCE = re.compile(r"^\s*```[\w-]*[ \t]*\r?\n?(.*?)\s*```\s*$", re.DOTALL)


def _strip_fence(text: str) -> str:
    """Drop a ```json ... ``` fence that surrounds the whole reply."""
    match = _FENCE.match(text)
    return match.group(1) if match else text


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
    """Schema-constrained generation validated by `schema_model`.

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
    conversation = _attach_images(messages, kw.pop("images_b64", None))
    name = schema_model.__name__

    reply = await chat(model, conversation, schema=schema, **kw)
    try:
        return schema_model.model_validate_json(_strip_fence(reply))
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
        return schema_model.model_validate_json(_strip_fence(reply))
    except ValidationError as exc:
        problems = _validation_problems(exc)
        log.warning("%s from %s still invalid after one retry: %s",
                    name, model, "; ".join(problems))
        raise ModelOutputInvalid(
            f"{name} output from {model!r} still invalid after one retry: "
            + "; ".join(problems),
            errors=problems, raw_output=reply,
        ) from exc


# --- model inventory + health ------------------------------------------------------

async def list_tags(*, timeout: float | None = None) -> list[str]:
    """Names of the locally pulled models (GET /api/tags)."""
    body = await _request("GET", "/api/tags", timeout=timeout or META_TIMEOUT_S)
    names: list[str] = []
    for entry in body.get("models") or []:
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("model")
            if isinstance(name, str) and name:
                names.append(name)
    return names


async def list_running(*, timeout: float | None = None) -> list[dict]:
    """Models currently loaded in memory (GET /api/ps "models" entries)."""
    body = await _request("GET", "/api/ps", timeout=timeout or META_TIMEOUT_S)
    return [entry for entry in body.get("models") or [] if isinstance(entry, dict)]


def placement(ps_entry: dict) -> str | None:
    """GPU/CPU split of a loaded model, same math as the PROCESSOR column of
    `ollama ps` (percentages rounded half away from zero). None if unknown."""
    size, vram = ps_entry.get("size"), ps_entry.get("size_vram")
    if not isinstance(size, (int, float)) or not isinstance(vram, (int, float)):
        return None
    if vram <= 0:
        return "100% CPU"
    if vram >= size:
        return "100% GPU"
    cpu = math.floor((size - vram) / size * 100 + 0.5)
    return f"{100 - cpu}% GPU / {cpu}% CPU"


def _canonical(name: str) -> str:
    """Ollama name for matching: lower-case, ":latest" if no tag given."""
    name = name.strip().lower()
    return name if ":" in name.rsplit("/", 1)[-1] else f"{name}:latest"


def _is_present(model: str | None, available: set[str]) -> bool:
    return bool(model and model.strip()) and _canonical(model) in available


def _configured_models() -> list[tuple[str, str]]:
    """[(role, model name)]; roles sharing a tag are merged ("medgemma+gemma4")."""
    merged: dict[str, tuple[list[str], str]] = {}
    for role, name in (("medgemma", settings.medgemma_model),
                       ("gemma4", settings.gemma4_model)):
        name = (name or "").strip()
        key = _canonical(name) if name else f"unset:{role}"
        if key in merged:
            merged[key][0].append(role)
        else:
            merged[key] = ([role], name)
    return [("+".join(roles), name) for roles, name in merged.values()]


async def _version() -> str | None:
    try:
        body = await _request("GET", "/api/version", timeout=META_TIMEOUT_S)
    except Exception:  # noqa: BLE001 - best effort, health probes never raise
        return None
    version = body.get("version")
    return version if isinstance(version, str) else None


async def _probe_latency_ms(model: str, timeout_s: float) -> float:
    """Round trip of a 5-token chat. The deadline also bounds waiting for a
    free generation slot; the reported time excludes that wait."""
    payload = _chat_payload(model, _PROBE_MESSAGES, schema=None, temperature=0,
                            images_b64=None, num_predict=PROBE_NUM_PREDICT)
    async with asyncio.timeout(timeout_s):
        body, elapsed = await _generate(model, payload, timeout_s)
    _message_content(model, body)
    return round(elapsed * 1000, 1)


async def probe_models(*, timeout_s: float = PROBE_TIMEOUT_S) -> dict:
    """Per-model status for GET /health/models. Never raises.

    {"ollama": "up"|"down", "pipeline", "num_ctx", "num_parallel", "version",
     ["error"], ["ps_error"],
     "models": [{"role", "name", "present", "loaded", "placement",
                 "latency_ms", "context_length", "error"}]}

    Each model is probed independently (sequentially, so the latencies do
    not compete). Probing loads a pulled-but-cold model (keep_alive 24h),
    which doubles as the warm-up; `loaded`/`placement` come from /api/ps
    after the probes.
    """
    report: dict[str, Any] = {
        "ollama": "down",
        "pipeline": settings.clinical_pipeline,
        "num_ctx": settings.ollama_num_ctx,
        "num_parallel": settings.ollama_num_parallel,
        "version": None,
        "models": [],
    }
    entries: list[dict[str, Any]] = [
        {"role": role, "name": name, "present": False, "loaded": False, "placement": None,
         "latency_ms": None, "context_length": None, "error": None}
        for role, name in _configured_models()
    ]
    report["models"] = entries

    try:
        available = {_canonical(t) for t in await list_tags()}
    except Exception as exc:  # noqa: BLE001 - never raises
        report["error"] = _describe(exc)
        for entry in entries:
            entry["error"] = f"Ollama unreachable: {report['error']}"
        return report
    report["ollama"] = "up"
    report["version"] = await _version()

    for entry in entries:
        name = entry["name"]
        if not name:
            entry["error"] = "no model configured for this role"
            continue
        entry["present"] = _canonical(name) in available
        if not entry["present"]:
            entry["error"] = f"model {name!r} is not pulled (missing from /api/tags)"
            continue
        try:
            entry["latency_ms"] = await _probe_latency_ms(name, timeout_s)
        except TimeoutError:
            entry["error"] = f"latency probe timed out after {timeout_s:g}s"
        except Exception as exc:  # noqa: BLE001 - never raises
            entry["error"] = _describe(exc)

    try:
        running = await list_running()
    except Exception as exc:  # noqa: BLE001 - never raises
        running = []
        report["ps_error"] = _describe(exc)
    loaded: dict[str, dict] = {}
    for item in running:
        for key in (item.get("name"), item.get("model")):
            if isinstance(key, str) and key:
                loaded.setdefault(_canonical(key), item)
    for entry in entries:
        item = loaded.get(_canonical(entry["name"])) if entry["name"] else None
        if item is not None:
            entry["loaded"] = True
            entry["placement"] = placement(item)
            entry["context_length"] = item.get("context_length")
    return report


async def health() -> dict:
    """Cheap liveness (tags only, short timeout). Never raises."""
    try:
        names = await list_tags(timeout=META_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - liveness must never raise
        return {"ollama": "down", "error": _describe(exc)}
    available = {_canonical(n) for n in names}
    return {
        "ollama": "up",
        "models": names,
        "medgemma_present": _is_present(settings.medgemma_model, available),
        "gemma4_present": _is_present(settings.gemma4_model, available),
    }
