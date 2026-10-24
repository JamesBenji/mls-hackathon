"""POST /inbound/voice-init (ElevenLabs conversation initiation for inbound calls)."""
from __future__ import annotations

import json

from sqlalchemy import select

from app import models
from app.adapters import elevenlabs
from app.services import context_builder
from tests.conftest import TOOL_TOKEN
from tests.test_webhooks import sign

AUTH = {"Authorization": f"Bearer {TOOL_TOKEN}"}


def payload(caller: str) -> dict:
    return {"caller_id": caller, "agent_id": "agent_test", "called_number": "+15559990000",
            "call_sid": "CA123"}


def test_known_caller_gets_personalized_variables(client, db, seeded):
    r = client.post("/inbound/voice-init", headers=AUTH, json=payload("+1 555-000-0001"))
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "conversation_initiation_client_data"
    dyn = body["dynamic_variables"]
    assert set(dyn) == set(elevenlabs.REQUIRED_DYNAMIC_VARIABLES)
    assert all(isinstance(v, str) for v in dyn.values())
    assert not any(k.startswith("system__") for k in dyn)
    assert dyn["patient_name"] == "Maria Gomez"
    assert dyn["resume_from"] == "start"
    assert dyn["question_plan"].startswith("1. Ask how you can help today")
    assert "Did you take your blood pressure today?" in dyn["question_plan"]
    assert "BP high" in dyn["last_call_summary"]
    db.expire_all()
    s = db.get(models.CallSession, int(dyn["session_id"]))
    assert s.direction == "inbound" and s.patient_id == seeded["maria_id"]
    assert s.status == "in_progress" and s.call_sid == "CA123"
    assert s.caller_number == "+15550000001"
    assert db.scalars(select(models.CallAttempt)
                      .where(models.CallAttempt.call_session_id == s.id)).one().attempt == 1


def test_unknown_caller_gets_safe_defaults(client, db, seeded):
    r = client.post("/inbound/voice-init", headers=AUTH, json=payload("+4915112345678"))
    assert r.status_code == 200
    dyn = r.json()["dynamic_variables"]
    assert dyn["patient_name"] == "caller"
    assert dyn["question_plan"] == context_builder.UNKNOWN_CALLER_PLAN
    assert dyn["last_call_summary"] == "none" and dyn["resume_from"] == "start"
    db.expire_all()
    s = db.get(models.CallSession, int(dyn["session_id"]))
    assert s.patient_id is None and s.direction == "inbound"
    assert s.caller_number == "+4915112345678"


def test_voice_init_requires_auth(client, seeded):
    assert client.post("/inbound/voice-init", json=payload("+15550000001")).status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert client.post("/inbound/voice-init", headers=bad,
                       json=payload("+15550000001")).status_code == 401


def test_voice_init_accepts_a_valid_elevenlabs_signature(client, seeded):
    raw = json.dumps(payload("+15550000002")).encode()
    r = client.post("/inbound/voice-init", content=raw,
                    headers={"elevenlabs-signature": sign(raw),
                             "Content-Type": "application/json"})
    assert r.status_code == 200
    assert r.json()["dynamic_variables"]["patient_name"] == "John Carter"


def test_voice_init_never_calls_the_model(client, seeded, monkeypatch):
    from app.services import clinical

    async def forbidden(*args, **kwargs):
        raise AssertionError("no LLM on the voice-init path")

    monkeypatch.setattr(clinical, "build_question_plan", forbidden)
    r = client.post("/inbound/voice-init", headers=AUTH, json=payload("+15550000001"))
    assert r.status_code == 200
