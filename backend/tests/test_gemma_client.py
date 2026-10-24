"""Model gateway (app/adapters/gemma_client.py): request shape, error
mapping, the structured-output retry, the concurrency gate and the health
probes against the Google Generative Language API. Everything talks to
in-process fakes (httpx.MockTransport); nothing touches the network.
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from app.adapters import gemma_client
from app.adapters.gemma_client import ModelError, ModelOutputInvalid, ModelUnavailable
from app.config import settings
from app.schemas.clinical import TriageResult
from tests.fakes import FakeGemma, chat_json_reply

MODEL = "gemma-4-31b-it"
MSGS = [
    {"role": "system", "content": "You are a terse assistant."},
    {"role": "user", "content": "Classify this call."},
]
VALID_TRIAGE = {"urgency": "monitor", "recommended_action": "doctor_callback",
                "rationale": "BP above target twice this week."}
SECRET = "Patient says SECRET-PROMPT-7731"


def _ok(content: str = "ok", finish: str = "STOP") -> httpx.Response:
    return httpx.Response(200, json={
        "candidates": [
            {"content": {"role": "model", "parts": [{"text": content}]},
             "finishReason": finish}
        ],
    })


def _install(handler) -> list[httpx.Request]:
    """Install a custom MockTransport (sync or async handler); returns the
    requests it saw. conftest resets the transport after every test."""
    seen: list[httpx.Request] = []

    async def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        result = handler(request)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    gemma_client.set_transport(httpx.MockTransport(wrapped))
    return seen


def _err(status: int, message: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"message": message, "code": status}})


# --- request shape ---------------------------------------------------------------

async def test_chat_hits_generatecontent_with_the_key_header(fake_gemma):
    fake_gemma.queue_chat("hello there")
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return await fake_gemma._handle(request)

    gemma_client.set_transport(httpx.MockTransport(handler))
    out = await gemma_client.chat(MODEL, MSGS)

    assert out == "hello there"
    assert seen[0].url.path.endswith(f"/models/{MODEL}:generateContent")
    assert seen[0].headers["x-goog-api-key"] == settings.gemma_api_key
    assert "key=" not in str(seen[0].url)  # the key never rides in the URL


async def test_contents_conversion_folds_the_system_prompt(fake_gemma):
    fake_gemma.queue_chat("ok")

    await gemma_client.chat(MODEL, MSGS)

    contents = fake_gemma.chat_requests[-1]["contents"]
    assert [c["role"] for c in contents] == ["user"]  # no system role for Gemma
    first_text = contents[0]["parts"][0]["text"]
    assert first_text.startswith("You are a terse assistant.")
    assert "Classify this call." in first_text


async def test_assistant_turns_become_model_role(fake_gemma):
    fake_gemma.queue_chat("ok")
    messages = [{"role": "user", "content": "one"},
               {"role": "assistant", "content": "two"},
               {"role": "user", "content": "three"}]

    await gemma_client.chat(MODEL, messages)

    contents = fake_gemma.chat_requests[-1]["contents"]
    assert [c["role"] for c in contents] == ["user", "model", "user"]
    assert contents[1]["parts"][0]["text"] == "two"


async def test_generation_config_carries_temperature_and_max_tokens(fake_gemma):
    fake_gemma.queue_chat("ok")

    await gemma_client.chat(MODEL, MSGS, temperature=0.5, num_predict=7)

    config = fake_gemma.chat_requests[-1]["generationConfig"]
    assert config == {"temperature": 0.5, "maxOutputTokens": 7}


async def test_schema_appends_a_json_instruction_to_the_last_user_turn(fake_gemma):
    fake_gemma.queue_chat("{}")
    schema = {"type": "object", "properties": {"urgency": {"type": "string"}}}

    await gemma_client.chat(MODEL, MSGS, schema=schema, temperature=0)

    text = fake_gemma.chat_requests[-1]["contents"][-1]["parts"][0]["text"]
    assert "valid JSON" in text
    assert json.dumps(schema) in text
    # the caller's messages are never mutated
    assert "valid JSON" not in MSGS[-1]["content"]


async def test_images_go_on_the_last_user_turn_as_inline_data(fake_gemma):
    fake_gemma.queue_chat("ok")
    conversation = [{"role": "system", "content": "s"},
                    {"role": "user", "content": "compare these"},
                    {"role": "assistant", "content": "sure"},
                    {"role": "user", "content": "now"}]
    images = ["data:image/png;base64,QUJD", "REVGR0g="]  # second one is bare base64

    await gemma_client.chat(MODEL, conversation, images_b64=images)

    last = fake_gemma.chat_requests[-1]["contents"][-1]
    assert last["role"] == "user"
    inline = [p for p in last["parts"] if "inline_data" in p]
    assert [p["inline_data"]["mime_type"] for p in inline] == ["image/png", "image/jpeg"]
    assert inline[0]["inline_data"]["data"] == "QUJD"
    # the caller's conversation is never mutated
    assert "images" not in conversation[-1]


async def test_images_without_a_user_message_get_their_own_turn(fake_gemma):
    fake_gemma.queue_chat("ok")

    await gemma_client.chat(MODEL, [{"role": "system", "content": "s"}],
                            images_b64=["QUJD"])

    contents = fake_gemma.chat_requests[-1]["contents"]
    assert contents[-1]["role"] == "user"
    assert any("inline_data" in p for p in contents[-1]["parts"])


# --- error mapping ----------------------------------------------------------------

@pytest.mark.parametrize("exc", [
    httpx.ConnectError("[Errno 111] Connection refused"),
    httpx.ReadTimeout("read timed out"),
    httpx.RemoteProtocolError("server disconnected"),
])
async def test_transport_errors_are_unavailable(fake_gemma, exc):
    fake_gemma.fail = exc

    with pytest.raises(ModelUnavailable):
        await gemma_client.chat(MODEL, MSGS)


async def test_slow_api_times_out_as_unavailable(fake_gemma):
    fake_gemma.delay_s = 2.0
    started = time.perf_counter()

    with pytest.raises(ModelUnavailable, match="timed out"):
        await gemma_client.chat(MODEL, MSGS, timeout=0.05)

    assert time.perf_counter() - started < 1.5  # the deadline, not the delay


async def test_default_timeout_comes_from_settings(fake_gemma, monkeypatch):
    fake_gemma.delay_s = 2.0
    monkeypatch.setattr(settings, "model_timeout_s", 0.05)

    with pytest.raises(ModelUnavailable, match="timed out"):
        await gemma_client.chat(MODEL, MSGS)


async def test_unknown_model_404_is_unavailable_and_names_the_model():
    _install(lambda request: _err(404, f"model {MODEL} not found"))

    with pytest.raises(ModelUnavailable) as info:
        await gemma_client.chat(MODEL, MSGS)

    assert MODEL in str(info.value)


@pytest.mark.parametrize("status", [401, 403, 429, 500, 502, 503])
async def test_auth_rate_limit_and_5xx_are_unavailable(status):
    _install(lambda request: _err(status, "backend exploded"))

    with pytest.raises(ModelUnavailable):
        await gemma_client.chat(MODEL, MSGS)


async def test_bad_api_key_400_is_unavailable():
    _install(lambda request: _err(400, "API key not valid. Please pass a valid API key."))

    with pytest.raises(ModelUnavailable, match="API key"):
        await gemma_client.chat(MODEL, MSGS)


async def test_other_400_is_model_error_not_unavailable():
    _install(lambda request: _err(400, "invalid request body"))

    with pytest.raises(ModelError) as info:
        await gemma_client.chat(MODEL, MSGS)

    assert not isinstance(info.value, ModelUnavailable)
    assert "invalid request body" in str(info.value)


@pytest.mark.parametrize("response", [
    httpx.Response(200, json={"candidates": []}),
    httpx.Response(200, json={"candidates": [{"content": {"parts": []}}]}),
    httpx.Response(200, json={"candidates": [{"content": {}}]}),
    httpx.Response(200, text="<html>not json</html>"),
    httpx.Response(200, json=["not", "an", "object"]),
])
async def test_malformed_reply_is_model_error(response):
    _install(lambda request: response)

    with pytest.raises(ModelError) as info:
        await gemma_client.chat(MODEL, MSGS)

    assert not isinstance(info.value, ModelUnavailable)


async def test_safety_blocked_reply_is_model_error():
    _install(lambda request: _ok("partial", finish="SAFETY"))

    with pytest.raises(ModelError, match="SAFETY"):
        await gemma_client.chat(MODEL, MSGS)


async def test_max_tokens_reply_is_returned_with_a_warning(fake_gemma):
    fake_gemma.queue_chat("truncated text")

    out = await gemma_client.chat(MODEL, MSGS)  # num_predict unset: warns, still returns

    assert out == "truncated text"


@pytest.mark.parametrize("status", [400, 404, 500])
async def test_error_messages_never_contain_the_prompt(status):
    _install(lambda request: _err(status, "nope"))

    with pytest.raises(ModelError) as info:
        await gemma_client.chat(MODEL, [{"role": "user", "content": SECRET}])

    assert "SECRET-PROMPT-7731" not in str(info.value)


# --- structured output ------------------------------------------------------------

async def test_chat_json_valid_first_try(fake_gemma):
    fake_gemma.queue_chat(chat_json_reply(VALID_TRIAGE))

    out = await gemma_client.chat_json(MODEL, MSGS, TriageResult)

    assert out.urgency == "monitor"
    assert len(fake_gemma.chat_requests) == 1
    config = fake_gemma.chat_requests[0]["generationConfig"]
    assert config["temperature"] == 0
    assert "valid JSON" in fake_gemma.chat_requests[0]["contents"][-1]["parts"][0]["text"]


async def test_chat_json_passes_overrides_through(fake_gemma):
    fake_gemma.queue_chat(chat_json_reply(VALID_TRIAGE))

    await gemma_client.chat_json(MODEL, MSGS, TriageResult, timeout=3, temperature=0.4)

    config = fake_gemma.chat_requests[0]["generationConfig"]
    assert config == {"temperature": 0.4, "maxOutputTokens": 3} or config["temperature"] == 0.4


async def test_chat_json_retries_once_with_the_validation_error(fake_gemma):
    bad = json.dumps({"urgency": "watch_tv", "recommended_action": "x",
                      "rationale": "y"})
    fake_gemma.queue_chat(bad, chat_json_reply(VALID_TRIAGE))

    out = await gemma_client.chat_json(MODEL, MSGS, TriageResult)

    assert out.urgency == "monitor"
    assert len(fake_gemma.chat_requests) == 2
    first, retry = fake_gemma.chat_requests
    assert first["contents"][-1]["parts"][0]["text"].startswith("Classify this call.")
    retry_texts = [p["text"] for p in retry["contents"][-1]["parts"] if "text" in p]
    assert any("did not match the required JSON schema" in t for t in retry_texts)


async def test_chat_json_retries_on_malformed_json(fake_gemma):
    fake_gemma.queue_chat("Sure! The urgency is monitor.", chat_json_reply(VALID_TRIAGE))

    out = await gemma_client.chat_json(MODEL, MSGS, TriageResult)

    assert out.urgency == "monitor"
    assert len(fake_gemma.chat_requests) == 2


async def test_chat_json_invalid_twice_raises_output_invalid(fake_gemma):
    bad = json.dumps({"urgency": "watch_tv", "recommended_action": "x",
                      "rationale": "y"})
    fake_gemma.queue_chat(bad, bad, chat_json_reply(VALID_TRIAGE))

    with pytest.raises(ModelOutputInvalid) as info:
        await gemma_client.chat_json(MODEL, MSGS, TriageResult)

    assert info.value.errors  # "field: message" lines for debugging
    assert len(fake_gemma.chat_requests) == 2  # exactly one retry, no third call


@pytest.mark.parametrize("wrap", [
    "```json\n{}\n```",
    "```\n{}\n```",
    "Here you go:\n```json\n{}\n```\nDone.",
])
async def test_chat_json_accepts_fenced_json(fake_gemma, wrap):
    fake_gemma.queue_chat(wrap.replace("{}", json.dumps(VALID_TRIAGE)))

    out = await gemma_client.chat_json(MODEL, MSGS, TriageResult)

    assert out.urgency == "monitor"
    assert len(fake_gemma.chat_requests) == 1


async def test_chat_json_propagates_unavailable_without_retry(fake_gemma):
    fake_gemma.fail = httpx.ConnectError("connection refused")

    with pytest.raises(ModelUnavailable):
        await gemma_client.chat_json(MODEL, MSGS, TriageResult)

    assert fake_gemma.chat_requests == []


async def test_chat_json_unavailable_during_retry_propagates(fake_gemma):
    fake_gemma.queue_chat("not json", httpx.ConnectError("connection refused"))

    with pytest.raises(ModelUnavailable):
        await gemma_client.chat_json(MODEL, MSGS, TriageResult)

    assert len(fake_gemma.chat_requests) == 1


async def test_chat_json_keeps_images_on_the_retry_conversation(fake_gemma):
    fake_gemma.queue_chat("not json", chat_json_reply(VALID_TRIAGE))
    messages = [{"role": "user", "content": "compare"}]

    out = await gemma_client.chat_json(MODEL, messages, TriageResult,
                                       images_b64=["QUJD"])

    assert out.urgency == "monitor"
    first, retry = fake_gemma.chat_requests
    assert any("inline_data" in p for p in first["contents"][-1]["parts"])
    assert any("inline_data" in p for p in retry["contents"][-1]["parts"])


async def test_chat_json_rejects_explicit_schema(fake_gemma):
    with pytest.raises(TypeError):
        await gemma_client.chat_json(MODEL, MSGS, TriageResult, schema={})


# --- concurrency gate ---------------------------------------------------------

def _concurrency_probe():
    state = {"inflight": 0, "peak": 0, "calls": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        state["inflight"] += 1
        state["peak"] = max(state["peak"], state["inflight"])
        await asyncio.sleep(0.03)
        state["inflight"] -= 1
        return _ok()

    gemma_client.set_transport(httpx.MockTransport(handler))
    return state


async def test_gate_caps_concurrent_generations(monkeypatch):
    state = _concurrency_probe()

    for limit in (2, 1, 3):  # changes are picked up within the same loop
        monkeypatch.setattr(settings, "model_max_concurrent", limit)
        state["peak"] = 0
        results = await asyncio.gather(
            *(gemma_client.chat(MODEL, MSGS) for _ in range(7)))
        assert results == ["ok"] * 7
        assert state["peak"] == limit


def test_gate_is_per_event_loop(monkeypatch):
    # With a single slot every burst has to wait on the semaphore; a
    # semaphore bound to the first loop would raise RuntimeError in the second.
    monkeypatch.setattr(settings, "model_max_concurrent", 1)
    state = _concurrency_probe()

    async def burst():
        return await asyncio.gather(*(gemma_client.chat(MODEL, MSGS) for _ in range(3)))

    assert asyncio.run(burst()) == ["ok"] * 3
    assert asyncio.run(burst()) == ["ok"] * 3
    assert state["calls"] == 6
    assert state["peak"] == 1


async def test_metadata_calls_are_not_gated(fake_gemma, monkeypatch):
    monkeypatch.setattr(settings, "model_max_concurrent", 1)
    fake_gemma.delay_s = 0.1
    started = time.perf_counter()

    await asyncio.gather(*(gemma_client.list_models() for _ in range(5)))

    assert time.perf_counter() - started < 0.35  # side by side; gated would take 0.5 s


# --- inventory + health ----------------------------------------------------------

async def test_list_models_strips_the_prefix(fake_gemma):
    assert await gemma_client.list_models() == ["gemma-4-31b-it"]


async def test_list_models_when_down(fake_gemma):
    fake_gemma.fail = httpx.ConnectError("connection refused")

    with pytest.raises(ModelUnavailable):
        await gemma_client.list_models()


async def test_probe_models_all_good(fake_gemma):
    report = await gemma_client.probe_models()

    assert report["gemma"] == "up"
    assert report["model"] == MODEL
    assert report["present"] is True
    assert isinstance(report["latency_ms"], float)
    assert report["error"] is None
    # the latency probe is a real (tiny) generation
    assert len(fake_gemma.chat_requests) == 1


async def test_probe_models_missing_model(fake_gemma, monkeypatch):
    fake_gemma.models = ["some-other-model"]

    report = await gemma_client.probe_models()

    assert report["gemma"] == "up"
    assert report["present"] is False
    assert "not available" in report["error"]
    assert fake_gemma.chat_requests == []  # never probed


async def test_probe_models_when_api_is_down(fake_gemma):
    fake_gemma.fail = httpx.ConnectError("connection refused")

    report = await gemma_client.probe_models()

    assert report["gemma"] == "down"
    assert report["error"]


async def test_probe_models_without_an_api_key(fake_gemma, monkeypatch):
    monkeypatch.setattr(settings, "gemma_api_key", None)

    report = await gemma_client.probe_models()

    assert report["gemma"] == "down"
    assert "GOOGLE_API_KEY" in report["error"]
    assert fake_gemma.chat_requests == []


async def test_health_up(fake_gemma):
    body = await gemma_client.health()

    assert body["gemma"] == "up"
    assert body["model_present"] is True
    assert fake_gemma.chat_requests == []  # cheap: no generation


async def test_health_reports_missing_model(fake_gemma):
    fake_gemma.models = ["some-other-model"]

    body = await gemma_client.health()

    assert body["gemma"] == "up"
    assert body["model_present"] is False


@pytest.mark.parametrize("fail", [
    httpx.ConnectError("connection refused"),
    httpx.ReadTimeout("read timed out"),
])
async def test_health_down_never_raises(fake_gemma, fail):
    fake_gemma.fail = fail

    assert (await gemma_client.health())["gemma"] == "down"
