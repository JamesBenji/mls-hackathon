"""X-API-Key protection of the routes in this workstream, open liveness,
Twilio-signature auth on /inbound/sms, and GET /health/models."""
from __future__ import annotations

import pytest
from twilio.request_validator import RequestValidator

from app.config import settings
from app.schemas.clinical import CallJobDraft, CallJobExtraction
from app.services import clinical
from tests.conftest import TWILIO_TOKEN

KEY = "k"


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setattr(settings, "guardian_api_key", KEY)
    return KEY


@pytest.fixture
def requests_(seeded, monkeypatch):
    async def fake_extract(text, doctors_patients, *, now=None):
        return CallJobExtraction(
            draft=CallJobDraft(patient_ref="Maria", situation="s", treatment="t",
                               questions=["q?"], scheduled_at=None),
            patient_id=seeded["maria_id"])

    monkeypatch.setattr(clinical, "extract_call_job", fake_extract)
    mid = seeded["maria_id"]
    return [
        ("get", "/patients", None),
        ("get", f"/patients/{mid}", None),
        ("patch", f"/patients/{mid}", {"category": "post-op"}),
        ("get", f"/patients/{mid}/vitals", None),
        ("post", f"/patients/{mid}/vitals", {"type": "weight", "value": 70}),
        ("get", f"/patients/{mid}/reports", None),
        ("get", "/triage", None),
        ("post", "/sms/send", {"patient_id": mid, "body": "hello"}),
        ("post", "/agent/jobs", {"doctor_id": seeded["doctor_id"], "text": "call Maria"}),
        ("get", "/agent/jobs", None),
    ]


def _send(client, method, path, body, headers):
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    return getattr(client, method)(path, **kwargs)


def test_protected_routes_need_api_key(client, api_key, requests_):
    for method, path, body in requests_:
        assert _send(client, method, path, body, {}).status_code == 401, path
        assert _send(client, method, path, body, {"X-API-Key": "wrong"}).status_code == 401, path
        r = _send(client, method, path, body, {"X-API-Key": KEY})
        assert r.status_code == 200, (path, r.text)


def test_health_models_needs_api_key(client, api_key, fake_gemma):
    assert client.get("/health/models").status_code == 401
    assert client.get("/health/models", headers={"X-API-Key": "nope"}).status_code == 401
    assert client.get("/health/models", headers={"X-API-Key": KEY}).status_code == 200


def test_health_is_open_and_dataless(client, api_key):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_inbound_sms_judged_by_twilio_signature_not_api_key(client, api_key, seeded,
                                                            monkeypatch):
    from app.services import redflags
    monkeypatch.setattr(redflags, "scan_text", lambda text: [])
    params = {"From": "+15550000001", "Body": "hello", "MessageSid": "SMauth"}
    # A valid API key does not replace the Twilio signature.
    r = client.post("/inbound/sms", data=params, headers={"X-API-Key": KEY})
    assert r.status_code == 403
    # A valid signature is enough, no API key needed.
    sig = RequestValidator(TWILIO_TOKEN).compute_signature(
        "https://guardian.test/inbound/sms", params)
    r = client.post("/inbound/sms", data=params, headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")


def test_health_models_report(client, fake_gemma):
    r = client.get("/health/models")
    assert r.status_code == 200
    body = r.json()
    assert body["gemma"] == "up"
    assert body["model"] == "gemma-4-31b-it"
    assert body["present"] is True
    assert isinstance(body["latency_ms"], float)
    assert body["telephony_mock"] is True  # MOCK_TELEPHONY=true in tests
    assert body["sms_mock"] is True
    assert body["scheduler_running"] is False  # SCHEDULER_ENABLED=false in tests


def test_health_models_when_gemma_down(client, fake_gemma):
    import httpx
    fake_gemma.fail = httpx.ConnectError("down")
    body = client.get("/health/models").json()
    assert body["gemma"] == "down"
    assert body["error"]
