"""ElevenLabs adapter: outbound-call payload, errors and mock mode; post-call
webhook signature verification.

No network: outbound requests go to an httpx.MockTransport recorder.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time

import httpx
import pytest

from app.adapters import elevenlabs
from app.config import settings
from tests.conftest import WEBHOOK_SECRET

API_KEY = "xi-test-key-123"
NUMBER = "+15550000001"
VARS = {
    "patient_name": "Maria Gomez",
    "question_plan": "1. Did you take your blood pressure today?",
    "last_call_summary": "none",
    "session_id": "42",
    "resume_from": "start",
}
SUCCESS = {"success": True, "message": "Success", "conversation_id": "conv_123",
           "callSid": "CA" + "0" * 32}


class Recorder:
    """httpx.MockTransport handler: records requests, replays `reply`."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.reply: httpx.Response | Exception = httpx.Response(200, json=SUCCESS)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def sent_variables(self) -> dict:
        body = json.loads(self.requests[-1].content)
        return body["conversation_initiation_client_data"]["dynamic_variables"]


@pytest.fixture
def recorder():
    rec = Recorder()
    elevenlabs.set_transport(httpx.MockTransport(rec))
    yield rec
    elevenlabs.set_transport(None)


@pytest.fixture
def live(monkeypatch, recorder):
    """Real (non-mock) mode with fake credentials; HTTP goes to `recorder`."""
    monkeypatch.setattr(settings, "mock_telephony", False)
    monkeypatch.setattr(settings, "eleven_api_key", API_KEY)
    monkeypatch.setattr(settings, "eleven_agent_id", "agent_test")
    monkeypatch.setattr(settings, "eleven_agent_phone_number_id", "phnum_test")
    monkeypatch.setattr(settings, "eleven_base_url", "https://eleven.test/")
    return recorder


# --- place_outbound_call: real path -------------------------------------------

async def test_outbound_call_posts_documented_payload(live):
    result = await elevenlabs.place_outbound_call(NUMBER, {**VARS, "session_id": 42})

    assert result == {"conversation_id": "conv_123", "call_sid": "CA" + "0" * 32,
                      "mock": False}
    [request] = live.requests
    assert request.method == "POST"
    assert str(request.url) == "https://eleven.test/v1/convai/twilio/outbound-call"
    assert request.headers["xi-api-key"] == API_KEY
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {
        "agent_id": "agent_test",
        "agent_phone_number_id": "phnum_test",
        "to_number": NUMBER,
        "conversation_initiation_client_data": {"dynamic_variables": {
            "patient_name": "Maria Gomez",
            "question_plan": "1. Did you take your blood pressure today?",
            "last_call_summary": "none",
            "session_id": "42",
            "resume_from": "start",
        }},
    }


async def test_system_variables_are_never_sent(live, caplog):
    caplog.set_level(logging.WARNING, logger=elevenlabs.__name__)
    await elevenlabs.place_outbound_call(
        NUMBER, {**VARS, "system__conversation_id": "x", "SYSTEM__time": "y"})
    assert set(live.sent_variables()) == set(VARS)
    assert "system__conversation_id" in caplog.text  # dropped with a warning


async def test_extra_variables_pass_through_as_strings_and_none_is_dropped(live):
    await elevenlabs.place_outbound_call(
        NUMBER, {**VARS, "attempt": 2, "is_callback": True, "unused": None})
    sent = live.sent_variables()
    assert sent["attempt"] == "2" and sent["is_callback"] == "True"
    assert "unused" not in sent
    assert all(isinstance(value, str) for value in sent.values())


async def test_formatted_number_is_sent_as_e164(live):
    await elevenlabs.place_outbound_call("+1 (555) 000-0001", VARS)
    assert json.loads(live.requests[0].content)["to_number"] == NUMBER


async def test_success_false_raises_with_provider_message(live):
    live.reply = httpx.Response(200, json={
        "success": False, "message": "Phone number is not assigned to an agent",
        "conversation_id": None, "callSid": None})
    with pytest.raises(elevenlabs.ElevenLabsError, match="not assigned to an agent"):
        await elevenlabs.place_outbound_call(NUMBER, VARS)


@pytest.mark.parametrize("status", [400, 401, 422, 429, 500, 503])
async def test_http_error_raises_with_status_but_never_the_api_key(live, status):
    live.reply = httpx.Response(status, json={"detail": {"message": f"boom key={API_KEY}"}})
    with pytest.raises(elevenlabs.ElevenLabsError) as info:
        await elevenlabs.place_outbound_call(NUMBER, VARS)
    message = str(info.value)
    assert str(status) in message and "boom" in message
    assert API_KEY not in message


async def test_huge_error_body_is_truncated(live):
    live.reply = httpx.Response(502, text="x" * 10_000)
    with pytest.raises(elevenlabs.ElevenLabsError) as info:
        await elevenlabs.place_outbound_call(NUMBER, VARS)
    assert "502" in str(info.value) and len(str(info.value)) < 1000


async def test_non_json_success_response_raises(live):
    live.reply = httpx.Response(200, text="<html>bad gateway</html>")
    with pytest.raises(elevenlabs.ElevenLabsError):
        await elevenlabs.place_outbound_call(NUMBER, VARS)


@pytest.mark.parametrize("error", [httpx.ConnectError("connection refused"),
                                   httpx.ReadTimeout("timed out")])
async def test_transport_errors_become_elevenlabs_errors(live, error):
    live.reply = error
    with pytest.raises(elevenlabs.ElevenLabsError):
        await elevenlabs.place_outbound_call(NUMBER, VARS)


async def test_malformed_base_url_becomes_elevenlabs_error(live, monkeypatch):
    monkeypatch.setattr(settings, "eleven_base_url", "http://[::1")
    with pytest.raises(elevenlabs.ElevenLabsError):
        await elevenlabs.place_outbound_call(NUMBER, VARS)
    assert live.requests == []


# --- place_outbound_call: input validation -------------------------------------

@pytest.mark.parametrize("name", list(VARS))
async def test_missing_required_variable_raises_before_any_request(live, name):
    without = {k: v for k, v in VARS.items() if k != name}
    with pytest.raises(elevenlabs.ElevenLabsError, match=name):
        await elevenlabs.place_outbound_call(NUMBER, without)
    with pytest.raises(elevenlabs.ElevenLabsError, match=name):
        await elevenlabs.place_outbound_call(NUMBER, {**VARS, name: None})
    assert live.requests == []


@pytest.mark.parametrize("number", ["015112345678", "12345", "+0123456789", "call me", "", None,
                                    15550000001])
async def test_non_e164_number_raises_before_any_request(live, number):
    with pytest.raises(elevenlabs.ElevenLabsError, match="E.164"):
        await elevenlabs.place_outbound_call(number, VARS)
    assert live.requests == []


def test_invalid_input_error_is_also_a_value_error():
    assert issubclass(elevenlabs.InvalidCallRequest, elevenlabs.ElevenLabsError)
    assert issubclass(elevenlabs.InvalidCallRequest, ValueError)


async def test_validation_also_applies_in_mock_mode(recorder):
    assert elevenlabs.is_mock()
    with pytest.raises(elevenlabs.InvalidCallRequest):
        await elevenlabs.place_outbound_call(NUMBER, {"patient_name": "Maria"})
    with pytest.raises(elevenlabs.InvalidCallRequest):
        await elevenlabs.place_outbound_call("12345", VARS)


# --- place_outbound_call: mock mode ---------------------------------------------

async def test_mock_mode_returns_unique_mock_ids_without_network(recorder):
    assert settings.mock_telephony is True  # conftest default
    first = await elevenlabs.place_outbound_call(NUMBER, VARS)
    second = await elevenlabs.place_outbound_call(NUMBER, VARS)

    assert first["mock"] is True
    assert re.fullmatch(r"mock-conv-[0-9a-f]{32}", first["conversation_id"])
    assert re.fullmatch(r"mock-call-[0-9a-f]{32}", first["call_sid"])
    assert first["conversation_id"] != second["conversation_id"]
    assert first["call_sid"] != second["call_sid"]
    assert recorder.requests == []


async def test_mock_mode_masks_number_in_logs(recorder, caplog):
    caplog.set_level(logging.INFO, logger=elevenlabs.__name__)
    await elevenlabs.place_outbound_call(NUMBER, VARS)
    assert NUMBER not in caplog.text
    assert "+15*****0001" in caplog.text


async def test_mock_flag_wins_over_complete_credentials(live, monkeypatch):
    monkeypatch.setattr(settings, "mock_telephony", True)
    assert elevenlabs.is_mock()
    result = await elevenlabs.place_outbound_call(NUMBER, VARS)
    assert result["mock"] is True and live.requests == []


@pytest.mark.parametrize("missing", [
    "eleven_api_key", "eleven_agent_id", "eleven_agent_phone_number_id"])
async def test_missing_credentials_fall_back_to_mock_with_warning(live, monkeypatch, caplog,
                                                                  missing):
    monkeypatch.setattr(settings, missing, "")
    caplog.set_level(logging.WARNING, logger=elevenlabs.__name__)
    assert elevenlabs.is_mock()
    result = await elevenlabs.place_outbound_call(NUMBER, VARS)
    assert result["mock"] is True and live.requests == []
    assert missing.upper() in caplog.text  # names the missing env var


def test_is_mock_false_with_credentials_and_flag_off(live):
    assert elevenlabs.is_mock() is False


# --- verify_webhook / sign_webhook ---------------------------------------------

EVENT = {
    "type": "post_call_transcription",
    "event_timestamp": 1739537297,
    "data": {
        "agent_id": "agent_test",
        "conversation_id": "conv_123",
        "status": "done",
        "transcript": [{"role": "agent", "message": "Hallo Frau Müller, wie geht es Ihnen?"},
                       {"role": "user", "message": "Gut, danke."}],
        "conversation_initiation_client_data": {"dynamic_variables": {"session_id": "42"}},
    },
}
BODY = json.dumps(EVENT, ensure_ascii=False).encode("utf-8")  # non-ASCII on purpose


def documented_header(body: bytes, secret: str, timestamp: int) -> str:
    """ElevenLabs' scheme, implemented independently of the adapter:
    t=<unix>,v0=hex(HMAC-SHA256(secret, "<t>.<raw body>"))."""
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"t={timestamp},v0={digest.hexdigest()}"


def now() -> int:
    return int(time.time())


def test_verify_accepts_body_signed_with_documented_scheme():
    header = documented_header(BODY, WEBHOOK_SECRET, now())
    assert elevenlabs.verify_webhook(BODY, header) == EVENT


def test_sign_then_verify_round_trip():
    assert elevenlabs.verify_webhook(BODY, elevenlabs.sign_webhook(BODY, WEBHOOK_SECRET)) == EVENT


def test_sign_webhook_matches_documented_scheme_with_explicit_timestamp():
    assert (elevenlabs.sign_webhook(BODY, "secret", timestamp=1700000000)
            == documented_header(BODY, "secret", 1700000000))


def test_sign_webhook_output_is_accepted_by_the_official_sdk():
    from elevenlabs.client import ElevenLabs

    header = elevenlabs.sign_webhook(BODY, WEBHOOK_SECRET)
    sdk = ElevenLabs(api_key="unused").webhooks
    assert sdk.construct_event(BODY.decode("utf-8"), header, WEBHOOK_SECRET) == EVENT


def test_verify_rejects_tampered_body():
    header = documented_header(BODY, WEBHOOK_SECRET, now())
    tampered = BODY.replace(b"conv_123", b"conv_999")
    with pytest.raises(elevenlabs.WebhookVerificationError):
        elevenlabs.verify_webhook(tampered, header)


def test_verify_rejects_wrong_secret():
    header = documented_header(BODY, "not-the-configured-secret", now())
    with pytest.raises(elevenlabs.WebhookVerificationError):
        elevenlabs.verify_webhook(BODY, header)


@pytest.mark.parametrize("header", [None, "", "   "])
def test_verify_rejects_missing_header(header):
    with pytest.raises(elevenlabs.WebhookVerificationError):
        elevenlabs.verify_webhook(BODY, header)


@pytest.mark.parametrize("make_header", [
    lambda h: "garbage",
    lambda h: h.split(",")[0],                               # t= only
    lambda h: h.split(",")[1],                               # v0= only
    lambda h: h.replace("v0=", "v1="),                       # unknown scheme
    lambda h: "t=abc," + h.split(",")[1],                    # non-numeric t
    lambda h: "t=-5," + h.split(",")[1],
    lambda h: "t=," + h.split(",")[1],
    lambda h: h.split(",")[0] + ",v0=ü" + "0" * 63,          # non-ASCII signature
])
def test_verify_rejects_malformed_header(make_header):
    header = make_header(documented_header(BODY, WEBHOOK_SECRET, now()))
    with pytest.raises(elevenlabs.WebhookVerificationError):
        elevenlabs.verify_webhook(BODY, header)


@pytest.mark.parametrize("timestamp", ["9" * 20, "9" * 400, "9" * 5000],
                         ids=["20-digits", "400-digits", "5000-digits"])
def test_verify_rejects_absurd_timestamps_cleanly(timestamp):
    # Huge values must not escape as OverflowError / ValueError (-> 500).
    digest = hmac.new(WEBHOOK_SECRET.encode(), f"{timestamp}.".encode() + BODY,
                      hashlib.sha256).hexdigest()
    with pytest.raises(elevenlabs.WebhookVerificationError):
        elevenlabs.verify_webhook(BODY, f"t={timestamp},v0={digest}")


@pytest.mark.parametrize("offset_s, accepted", [
    (-29 * 60, True),   # inside the 30 min tolerance
    (-31 * 60, False),  # stale
    (60, True),         # small clock skew ahead
    (31 * 60, False),   # absurdly far in the future
])
def test_verify_enforces_timestamp_window(offset_s, accepted):
    header = documented_header(BODY, WEBHOOK_SECRET, now() + offset_s)
    if accepted:
        assert elevenlabs.verify_webhook(BODY, header) == EVENT
    else:
        with pytest.raises(elevenlabs.WebhookVerificationError):
            elevenlabs.verify_webhook(BODY, header)


@pytest.mark.parametrize("sdk_available", [True, False])
@pytest.mark.parametrize("configured", ["", None])
def test_verify_rejects_everything_when_secret_not_configured(monkeypatch, configured,
                                                              sdk_available):
    monkeypatch.setattr(settings, "eleven_webhook_secret", configured)
    if not sdk_available:
        monkeypatch.setattr(elevenlabs, "_sdk_construct_event", lambda: None)
    # Including a body signed with an empty key: what an attacker would try
    # against a server whose secret is unset.
    for secret in ("", WEBHOOK_SECRET):
        with pytest.raises(elevenlabs.WebhookVerificationError):
            elevenlabs.verify_webhook(BODY, documented_header(BODY, secret, now()))


def test_mock_mode_never_bypasses_verification():
    assert settings.mock_telephony is True and elevenlabs.is_mock()
    with pytest.raises(elevenlabs.WebhookVerificationError):
        elevenlabs.verify_webhook(BODY, None)
    with pytest.raises(elevenlabs.WebhookVerificationError):
        elevenlabs.verify_webhook(BODY, documented_header(BODY, "wrong", now()))


@pytest.mark.parametrize("body", [b"not json", b"[1, 2, 3]", b'"just a string"', b"",
                                  b'{"x": "\xff"}'])
def test_verify_rejects_signed_body_that_is_not_a_json_object(body):
    header = documented_header(body, WEBHOOK_SECRET, now())
    with pytest.raises(elevenlabs.WebhookVerificationError):
        elevenlabs.verify_webhook(body, header)


def test_verify_uses_official_sdk_helper(monkeypatch):
    real = elevenlabs._sdk_construct_event()
    assert real is not None  # the SDK is installed in this venv
    calls = []

    def spy(raw_body, sig_header, secret):
        calls.append((raw_body, sig_header, secret))
        return real(raw_body, sig_header, secret)

    monkeypatch.setattr(elevenlabs, "_sdk_construct_event", lambda: spy)
    header = documented_header(BODY, WEBHOOK_SECRET, now())
    assert elevenlabs.verify_webhook(BODY, header) == EVENT
    assert calls == [(BODY.decode("utf-8"), header, WEBHOOK_SECRET)]


def test_sdk_rejection_is_wrapped(monkeypatch):
    from elevenlabs.errors import BadRequestError
    from elevenlabs.types.bad_request_error_body import BadRequestErrorBody

    def reject(raw_body, sig_header, secret):
        raise BadRequestError(body=BadRequestErrorBody(error="Signature hash does not match"))

    monkeypatch.setattr(elevenlabs, "_sdk_construct_event", lambda: reject)
    with pytest.raises(elevenlabs.WebhookVerificationError, match="Signature hash does not match"):
        elevenlabs.verify_webhook(BODY, documented_header(BODY, WEBHOOK_SECRET, now()))


def test_builtin_verification_still_enforced_without_sdk(monkeypatch):
    monkeypatch.setattr(elevenlabs, "_sdk_construct_event", lambda: None)
    assert elevenlabs.verify_webhook(BODY, documented_header(BODY, WEBHOOK_SECRET, now())) == EVENT
    with pytest.raises(elevenlabs.WebhookVerificationError):
        elevenlabs.verify_webhook(BODY, documented_header(BODY, "wrong", now()))
    with pytest.raises(elevenlabs.WebhookVerificationError):
        elevenlabs.verify_webhook(BODY, documented_header(BODY, WEBHOOK_SECRET, now() - 31 * 60))
    with pytest.raises(elevenlabs.WebhookVerificationError):
        elevenlabs.verify_webhook(b"[]", documented_header(b"[]", WEBHOOK_SECRET, now()))
