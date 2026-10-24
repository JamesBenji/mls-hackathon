"""Model gateway (app/adapters/ollama_client.py, MEDGEMMA_DO_SETUP.md §5/§8/§10):
request shape, error mapping, the structured-output retry, the concurrency
gate and the health probes. Everything talks to in-process fakes
(httpx.MockTransport); nothing touches the network.
"""
from __future__ import annotations

import asyncio
import copy
import json
import time

import httpx
import pytest

from app.adapters import ollama_client
from app.adapters.ollama_client import ModelError, ModelOutputInvalid, ModelUnavailable
from app.config import settings
from app.schemas.clinical import TriageResult
from tests.fakes import FakeOllama, chat_json_reply

MSGS = [
    {"role": "system", "content": "You are a terse assistant."},
    {"role": "user", "content": "Classify this call."},
]
VALID_TRIAGE = {"urgency": "monitor", "recommended_action": "doctor_callback",
                "rationale": "BP above target twice this week."}
SECRET = "Patient says SECRET-PROMPT-7731"


def _ok(content: str = "ok") -> httpx.Response:
    return httpx.Response(200, json={"message": {"role": "assistant", "content": content},
                                     "done": True})


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

    ollama_client.set_transport(httpx.MockTransport(wrapped))
    return seen


def _route_chat(fake: FakeOllama, chat_handler) -> None:
    """FakeOllama for every path, but /api/chat answered by `chat_handler`
    (async, returns a Response or None to fall through to the fake)."""
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/chat":
            result = await chat_handler(request)
            if result is not None:
                return result
        return await fake._handle(request)

    ollama_client.set_transport(httpx.MockTransport(handler))


# --- request shape ---------------------------------------------------------------

async def test_chat_payload_shape(fake_ollama):
    fake_ollama.queue_chat("hello there")

    out = await ollama_client.chat("gemma4:12b", MSGS)

    assert out == "hello there"
    body = fake_ollama.chat_requests[-1]
    assert body["model"] == "gemma4:12b"
    assert body["messages"] == MSGS
    assert body["stream"] is False
    assert body["keep_alive"] == "24h"
    assert body["options"] == {"temperature": 0.2, "num_ctx": settings.ollama_num_ctx}
    assert "format" not in body  # free text unless a schema is given


async def test_chat_schema_becomes_format_and_num_predict_is_optional(fake_ollama):
    schema = TriageResult.model_json_schema()

    await ollama_client.chat("gemma4:12b", MSGS, schema=schema, temperature=0, num_predict=7)

    body = fake_ollama.chat_requests[-1]
    assert body["format"] == schema
    assert body["options"] == {"temperature": 0, "num_ctx": settings.ollama_num_ctx,
                               "num_predict": 7}


async def test_settings_are_read_at_call_time(monkeypatch):
    seen = _install(lambda request: _ok())
    monkeypatch.setattr(settings, "ollama_num_ctx", 8192)
    monkeypatch.setattr(settings, "ollama_url", "http://tunnel.test:11500")

    await ollama_client.chat("medgemma:4b", MSGS)

    assert str(seen[-1].url) == "http://tunnel.test:11500/api/chat"
    assert json.loads(seen[-1].content)["options"]["num_ctx"] == 8192


async def test_scheme_less_ollama_url_is_tolerated(monkeypatch):
    seen = _install(lambda request: httpx.Response(200, json={"models": []}))
    monkeypatch.setattr(settings, "ollama_url", "127.0.0.1:11434")  # OLLAMA_HOST style

    assert await ollama_client.list_tags() == []
    assert str(seen[-1].url) == "http://127.0.0.1:11434/api/tags"


async def test_images_go_on_last_user_message_without_mutating_caller(fake_ollama):
    conversation = [
        {"role": "system", "content": "Compare the two images."},
        {"role": "user", "content": "Prior upload."},
        {"role": "assistant", "content": "Noted."},
        {"role": "user", "content": "New upload.", "images": ["old64"]},
    ]
    snapshot = copy.deepcopy(conversation)

    await ollama_client.chat("medgemma:4b", conversation,
                             images_b64=["img64", "data:image/png;base64,png64"])

    sent = fake_ollama.chat_requests[-1]["messages"]
    assert sent[3]["images"] == ["old64", "img64", "png64"]  # data: prefix stripped
    assert all("images" not in m for m in sent[:3])
    assert conversation == snapshot  # caller's list and dicts untouched


async def test_images_without_a_user_message_get_their_own(fake_ollama):
    await ollama_client.chat("medgemma:4b", [{"role": "system", "content": "x"}],
                             images_b64=["img64"])

    sent = fake_ollama.chat_requests[-1]["messages"]
    assert sent[-1] == {"role": "user", "content": "", "images": ["img64"]}


# --- error mapping ---------------------------------------------------------------

@pytest.mark.parametrize("exc", [
    httpx.ConnectError("[Errno 111] Connection refused"),
    httpx.ReadTimeout("read timed out"),
    httpx.RemoteProtocolError("server disconnected"),
])
async def test_transport_errors_are_unavailable(fake_ollama, exc):
    fake_ollama.fail = exc

    with pytest.raises(ModelUnavailable):
        await ollama_client.chat("gemma4:12b", MSGS)


async def test_slow_model_times_out_as_unavailable(fake_ollama):
    fake_ollama.delay_s = 2.0
    started = time.perf_counter()

    with pytest.raises(ModelUnavailable, match="timed out"):
        await ollama_client.chat("gemma4:12b", MSGS, timeout=0.05)

    assert time.perf_counter() - started < 1.5  # the deadline, not the delay


async def test_default_timeout_comes_from_settings(fake_ollama, monkeypatch):
    fake_ollama.delay_s = 2.0
    monkeypatch.setattr(settings, "model_timeout_s", 0.05)

    with pytest.raises(ModelUnavailable, match="timed out"):
        await ollama_client.chat("gemma4:12b", MSGS)


async def test_unknown_model_404_is_unavailable_and_names_the_tag():
    _install(lambda request: httpx.Response(
        404, json={"error": "model 'medgemma:4b' not found, try pulling it first"}))

    with pytest.raises(ModelUnavailable) as info:
        await ollama_client.chat("medgemma:4b", MSGS)

    assert "medgemma:4b" in str(info.value)


@pytest.mark.parametrize("status", [500, 502, 503])
async def test_5xx_is_unavailable(status):
    _install(lambda request: httpx.Response(status, json={"error": "runner crashed"}))

    with pytest.raises(ModelUnavailable):
        await ollama_client.chat("gemma4:12b", MSGS)


@pytest.mark.parametrize("status", [400, 401, 422])
async def test_other_4xx_is_model_error_not_unavailable(status):
    _install(lambda request: httpx.Response(status, json={"error": "invalid format"}))

    with pytest.raises(ModelError) as info:
        await ollama_client.chat("gemma4:12b", MSGS)

    assert not isinstance(info.value, ModelUnavailable)
    assert "invalid format" in str(info.value)


@pytest.mark.parametrize("response", [
    httpx.Response(200, json={"done": True}),
    httpx.Response(200, json={"message": {"role": "assistant"}}),
    httpx.Response(200, json={"message": {"role": "assistant", "content": None}}),
    httpx.Response(200, text="<html>not ollama</html>"),
    httpx.Response(200, json=["not", "an", "object"]),
])
async def test_malformed_reply_is_model_error(response):
    _install(lambda request: response)

    with pytest.raises(ModelError) as info:
        await ollama_client.chat("gemma4:12b", MSGS)

    assert not isinstance(info.value, ModelUnavailable)


@pytest.mark.parametrize("status", [400, 404, 500])
async def test_error_messages_never_contain_the_prompt(status):
    _install(lambda request: httpx.Response(status, json={"error": "nope"}))

    with pytest.raises(ModelError) as info:
        await ollama_client.chat("gemma4:12b", [{"role": "user", "content": SECRET}])

    assert "SECRET-PROMPT-7731" not in str(info.value)


# --- chat_json -------------------------------------------------------------------

async def test_chat_json_valid_first_try(fake_ollama):
    fake_ollama.queue_chat(chat_json_reply(VALID_TRIAGE))

    result = await ollama_client.chat_json("gemma4:12b", MSGS, TriageResult)

    assert isinstance(result, TriageResult)
    assert result.urgency == "monitor"
    assert result.uncertain_because == []
    assert len(fake_ollama.chat_requests) == 1
    body = fake_ollama.chat_requests[0]
    assert body["format"] == TriageResult.model_json_schema()
    assert body["options"]["temperature"] == 0


async def test_chat_json_passes_overrides_through(fake_ollama):
    fake_ollama.queue_chat(chat_json_reply(VALID_TRIAGE))

    await ollama_client.chat_json("gemma4:12b", MSGS, TriageResult,
                                  temperature=0.3, num_predict=256)

    options = fake_ollama.chat_requests[0]["options"]
    assert options["temperature"] == 0.3
    assert options["num_predict"] == 256


async def test_chat_json_retries_once_with_the_validation_error(fake_ollama):
    bad = json.dumps({**VALID_TRIAGE, "urgency": "critical"})
    fake_ollama.queue_chat(bad, chat_json_reply(VALID_TRIAGE))

    result = await ollama_client.chat_json("gemma4:12b", MSGS, TriageResult)

    assert result.urgency == "monitor"
    first, retry = fake_ollama.chat_requests
    assert first["messages"] == MSGS
    assert retry["format"] == TriageResult.model_json_schema()
    assert retry["messages"][: len(MSGS)] == MSGS
    assert retry["messages"][len(MSGS)] == {"role": "assistant", "content": bad}
    correction = retry["messages"][-1]
    assert correction["role"] == "user"
    assert "urgency" in correction["content"]
    assert "Input should be" in correction["content"]  # pydantic's error text
    assert "ONLY valid JSON" in correction["content"]


async def test_chat_json_retries_on_malformed_json(fake_ollama):
    fake_ollama.queue_chat("Sure! The urgency is monitor.", chat_json_reply(VALID_TRIAGE))

    result = await ollama_client.chat_json("gemma4:12b", MSGS, TriageResult)

    assert result.recommended_action == "doctor_callback"
    assert "Invalid JSON" in fake_ollama.chat_requests[1]["messages"][-1]["content"]


async def test_chat_json_invalid_twice_raises_output_invalid(fake_ollama):
    bad = json.dumps({"urgency": "critical", "recommended_action": "none",
                      "rationale": SECRET})
    fake_ollama.queue_chat(bad, bad, chat_json_reply(VALID_TRIAGE))

    with pytest.raises(ModelOutputInvalid) as info:
        await ollama_client.chat_json("gemma4:12b",
                                      [{"role": "user", "content": SECRET}], TriageResult)

    assert len(fake_ollama.chat_requests) == 2  # exactly one retry, no third call
    err = info.value
    assert "TriageResult" in str(err)
    assert "SECRET-PROMPT-7731" not in str(err)  # neither prompt nor output leaks
    assert any("urgency" in line for line in err.errors)
    assert err.raw_output == bad


@pytest.mark.parametrize("wrap", [
    "```json\n{}\n```",
    "```\n{}\n```",
    "  ```JSON\r\n{}\r\n```  \n",
])
async def test_chat_json_accepts_fenced_json(fake_ollama, wrap):
    fake_ollama.queue_chat(wrap.replace("{}", json.dumps(VALID_TRIAGE)))

    result = await ollama_client.chat_json("gemma4:12b", MSGS, TriageResult)

    assert result.urgency == "monitor"
    assert len(fake_ollama.chat_requests) == 1


async def test_chat_json_propagates_unavailable_without_retry(fake_ollama):
    fake_ollama.fail = httpx.ConnectError("connection refused")

    with pytest.raises(ModelUnavailable) as info:
        await ollama_client.chat_json("gemma4:12b", MSGS, TriageResult)

    assert not isinstance(info.value, ModelOutputInvalid)


async def test_chat_json_unavailable_during_retry_propagates(fake_ollama):
    fake_ollama.queue_chat("not json", httpx.ConnectError("connection refused"))

    with pytest.raises(ModelUnavailable):
        await ollama_client.chat_json("gemma4:12b", MSGS, TriageResult)


async def test_chat_json_keeps_images_on_the_original_message(fake_ollama):
    fake_ollama.queue_chat("not json", chat_json_reply(VALID_TRIAGE))

    await ollama_client.chat_json("medgemma:4b", MSGS, TriageResult, images_b64=["img64"])

    first, retry = fake_ollama.chat_requests
    assert first["messages"][1]["images"] == ["img64"]
    assert retry["messages"][1]["images"] == ["img64"]
    assert "images" not in retry["messages"][-1]  # not moved to the correction
    assert "images" not in MSGS[1]


async def test_chat_json_rejects_explicit_schema(fake_ollama):
    with pytest.raises(TypeError):
        await ollama_client.chat_json("gemma4:12b", MSGS, TriageResult, schema={})


# --- concurrency gate ------------------------------------------------------------

def _concurrency_probe():
    state = {"inflight": 0, "peak": 0, "calls": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        state["inflight"] += 1
        state["peak"] = max(state["peak"], state["inflight"])
        await asyncio.sleep(0.03)
        state["inflight"] -= 1
        return _ok()

    ollama_client.set_transport(httpx.MockTransport(handler))
    return state


async def test_gate_caps_concurrent_generations(monkeypatch):
    state = _concurrency_probe()

    for limit in (2, 1, 3):  # changes are picked up within the same loop
        monkeypatch.setattr(settings, "ollama_num_parallel", limit)
        state["peak"] = 0
        results = await asyncio.gather(
            *(ollama_client.chat("gemma4:12b", MSGS) for _ in range(7)))
        assert results == ["ok"] * 7
        assert state["peak"] == limit


def test_gate_is_per_event_loop(monkeypatch):
    # With a single slot every burst has to wait on the semaphore; a
    # semaphore bound to the first loop would raise RuntimeError in the second.
    monkeypatch.setattr(settings, "ollama_num_parallel", 1)
    state = _concurrency_probe()

    async def burst():
        return await asyncio.gather(*(ollama_client.chat("gemma4:12b", MSGS) for _ in range(3)))

    assert asyncio.run(burst()) == ["ok"] * 3
    assert asyncio.run(burst()) == ["ok"] * 3
    assert state["calls"] == 6
    assert state["peak"] == 1


async def test_metadata_calls_are_not_gated(fake_ollama, monkeypatch):
    monkeypatch.setattr(settings, "ollama_num_parallel", 1)
    fake_ollama.delay_s = 0.1
    started = time.perf_counter()

    await asyncio.gather(*(ollama_client.list_tags() for _ in range(5)))

    assert time.perf_counter() - started < 0.35  # side by side; gated would take 0.5 s


# --- inventory -----------------------------------------------------------------------

async def test_list_tags_and_running(fake_ollama):
    assert await ollama_client.list_tags() == ["medgemma:4b", "gemma4:12b"]
    assert await ollama_client.list_running() == fake_ollama.running


async def test_list_tags_when_down(fake_ollama):
    fake_ollama.fail = httpx.ConnectError("connection refused")

    with pytest.raises(ModelUnavailable):
        await ollama_client.list_tags()


@pytest.mark.parametrize("size, vram, expected", [
    (100, 100, "100% GPU"),
    (100, 150, "100% GPU"),
    (100, 0, "100% CPU"),
    (0, 0, "100% CPU"),
    (100, 52, "52% GPU / 48% CPU"),
    (3, 1, "33% GPU / 67% CPU"),
    (8, 7, "87% GPU / 13% CPU"),  # 12.5% CPU rounds half away from zero, like ollama ps
    (None, 100, None),
    (100, None, None),
])
def test_placement_strings(size, vram, expected):
    assert ollama_client.placement({"size": size, "size_vram": vram}) == expected


# --- probe_models (/health/models) ---------------------------------------------------

async def test_probe_models_all_good(fake_ollama):
    report = await ollama_client.probe_models()

    assert report["ollama"] == "up"
    assert report["pipeline"] == settings.clinical_pipeline
    assert report["num_ctx"] == settings.ollama_num_ctx
    assert report["version"] == "0.22.0"
    assert [(m["role"], m["name"]) for m in report["models"]] == [
        ("medgemma", "medgemma:4b"), ("gemma4", "gemma4:12b")]
    for model in report["models"]:
        assert model["present"] is True
        assert model["loaded"] is True
        assert model["placement"] == "100% GPU"
        assert isinstance(model["latency_ms"], float) and model["latency_ms"] >= 0
        assert model["error"] is None
    probes = fake_ollama.chat_requests
    assert [p["model"] for p in probes] == ["medgemma:4b", "gemma4:12b"]
    assert all(p["options"]["num_predict"] == 5 for p in probes)
    assert all(p["options"]["num_ctx"] == settings.ollama_num_ctx for p in probes)


async def test_probe_models_reports_cpu_split(fake_ollama):
    fake_ollama.running = [
        {"name": "medgemma:4b", "model": "medgemma:4b", "size": 100, "size_vram": 52},
        {"name": "gemma4:12b", "model": "gemma4:12b", "size": 100, "size_vram": 0,
         "context_length": 16384},
    ]

    report = await ollama_client.probe_models()

    medgemma, gemma4 = report["models"]
    assert medgemma["placement"] == "52% GPU / 48% CPU"
    assert gemma4["placement"] == "100% CPU"
    assert gemma4["context_length"] == 16384


async def test_probe_models_missing_and_cold_models(fake_ollama):
    fake_ollama.tags = ["gemma4:12b"]
    fake_ollama.running = []

    report = await ollama_client.probe_models()

    medgemma, gemma4 = report["models"]
    assert medgemma["present"] is False
    assert medgemma["latency_ms"] is None
    assert "not pulled" in medgemma["error"]
    assert gemma4["present"] is True
    assert gemma4["loaded"] is False
    assert gemma4["placement"] is None
    assert gemma4["latency_ms"] is not None and gemma4["error"] is None
    assert [p["model"] for p in fake_ollama.chat_requests] == ["gemma4:12b"]


async def test_probe_models_when_ollama_is_down(fake_ollama):
    fake_ollama.fail = httpx.ConnectError("connection refused")

    report = await ollama_client.probe_models()  # must not raise

    assert report["ollama"] == "down"
    assert report["pipeline"] == settings.clinical_pipeline
    assert report["num_ctx"] == settings.ollama_num_ctx
    assert "error" in report
    assert len(report["models"]) == 2
    for model in report["models"]:
        assert model["present"] is False and model["loaded"] is False
        assert model["placement"] is None and model["latency_ms"] is None
        assert "unreachable" in model["error"]


async def test_probe_models_dedupes_a_shared_model(fake_ollama, monkeypatch):
    monkeypatch.setattr(settings, "medgemma_model", "gemma4:12b")  # CLINICAL_PIPELINE=single

    report = await ollama_client.probe_models()

    assert [(m["role"], m["name"]) for m in report["models"]] == [
        ("medgemma+gemma4", "gemma4:12b")]
    assert len(fake_ollama.chat_requests) == 1


async def test_probe_models_isolates_a_failing_model():
    fake = FakeOllama()

    async def chat(request: httpx.Request):
        if json.loads(request.content)["model"] == "medgemma:4b":
            return httpx.Response(500, json={"error": "model runner has unexpectedly stopped"})
        return None

    _route_chat(fake, chat)

    report = await ollama_client.probe_models()

    medgemma, gemma4 = report["models"]
    assert "500" in medgemma["error"] and medgemma["latency_ms"] is None
    assert gemma4["error"] is None and gemma4["latency_ms"] is not None


async def test_probe_models_latency_timeout():
    fake = FakeOllama()

    async def slow_chat(request: httpx.Request):
        await asyncio.sleep(2)

    _route_chat(fake, slow_chat)
    started = time.perf_counter()

    report = await ollama_client.probe_models(timeout_s=0.05)

    assert time.perf_counter() - started < 1.5
    for model in report["models"]:
        assert model["present"] is True and model["latency_ms"] is None
        assert "timed out" in model["error"]


async def test_probe_models_matches_implicit_latest_tag(fake_ollama, monkeypatch):
    monkeypatch.setattr(settings, "gemma4_model", "gemma4")
    fake_ollama.tags = ["medgemma:4b", "gemma4:latest"]
    fake_ollama.running = [{"name": "gemma4:latest", "model": "gemma4:latest",
                            "size": 10, "size_vram": 10}]

    report = await ollama_client.probe_models()

    gemma4 = report["models"][1]
    assert gemma4["present"] is True and gemma4["loaded"] is True
    assert gemma4["placement"] == "100% GPU"


# --- health (/health) ------------------------------------------------------------------

async def test_health_up(fake_ollama):
    assert await ollama_client.health() == {
        "ollama": "up",
        "models": ["medgemma:4b", "gemma4:12b"],
        "medgemma_present": True,
        "gemma4_present": True,
    }
    assert fake_ollama.chat_requests == []  # cheap: no generation


async def test_health_reports_missing_model(fake_ollama):
    fake_ollama.tags = ["gemma4:12b"]

    result = await ollama_client.health()

    assert result["ollama"] == "up"
    assert result["medgemma_present"] is False
    assert result["gemma4_present"] is True


@pytest.mark.parametrize("fail", [
    httpx.ConnectError("connection refused"),
    httpx.ReadTimeout("timed out"),
    RuntimeError("unexpected"),
])
async def test_health_down_never_raises(fake_ollama, fail):
    fake_ollama.fail = fail

    result = await ollama_client.health()

    assert result["ollama"] == "down"
    assert result["error"]


async def test_health_down_on_garbage_reply():
    _install(lambda request: httpx.Response(200, text="not json"))

    assert (await ollama_client.health())["ollama"] == "down"
