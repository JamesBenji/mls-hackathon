"""ElevenLabs agent tools: /tools/schedule_callback and /tools/flag_emergency."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app import models, timeutil
from app.config import settings
from app.services import call_orchestrator, scheduler
from tests.conftest import TOOL_TOKEN
from tests.test_webhooks import make_session

AUTH = {"Authorization": f"Bearer {TOOL_TOKEN}"}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"},
                                     {"Authorization": TOOL_TOKEN}])
@pytest.mark.parametrize("path", ["/tools/schedule_callback", "/tools/flag_emergency"])
def test_tools_require_the_bearer_token(client, db, seeded, headers, path):
    sid = make_session(db, seeded["maria_id"])
    r = client.post(path, json={"session_id": sid, "reason": "x",
                                "recommended_action": "doctor_callback"}, headers=headers)
    assert r.status_code == 401
    db.expire_all()
    assert db.get(models.CallSession, sid).status == "in_progress"


def test_schedule_callback_sets_state_job_and_sms(client, db, seeded):
    sid = make_session(db, seeded["maria_id"], conv_id="conv-a")
    before = timeutil.utcnow()
    r = client.post("/tools/schedule_callback", headers=AUTH, json={
        "session_id": str(sid), "minutes": 5, "missing_item": "blood pressure",
        "checkpoint": "Meds answered (took them 8am). Still need BP."})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    db.expire_all()
    s = db.get(models.CallSession, sid)
    assert s.status == "callback_pending"
    assert abs((s.callback_at - (before + timedelta(minutes=5))).total_seconds()) < 60
    assert body["callback_time"] == timeutil.hhmm_local(s.callback_at)
    assert s.checkpoint == {"text": "Meds answered (took them 8am). Still need BP.",
                            "missing_item": "blood pressure", "conversation_id": "conv-a"}
    assert scheduler.get_job(f"resume:{sid}") is not None
    sms = db.scalars(select(models.SmsLog)).one()
    assert sms.kind == "callback" and sms.patient_id == seeded["maria_id"]
    assert sms.body == (f"Guardian will call you back at {body['callback_time']} "
                        "to record your blood pressure.")


def test_schedule_callback_defaults(client, db, seeded):
    sid = make_session(db, seeded["maria_id"])
    before = timeutil.utcnow()
    r = client.post("/tools/schedule_callback", headers=AUTH, json={"session_id": sid})
    assert r.status_code == 200
    db.expire_all()
    s = db.get(models.CallSession, sid)
    expected = before + timedelta(minutes=settings.callback_default_minutes)
    assert abs((s.callback_at - expected).total_seconds()) < 60
    assert s.checkpoint["text"] == "" and s.checkpoint["missing_item"] is None
    assert db.scalars(select(models.SmsLog)).one().body.endswith("to record your reading.")


@pytest.mark.parametrize("raw,expected", [
    (None, 15), (10, 10), (2.6, 3), ("20", 20), ("in 30 minutes", 30), ("soon", 15),
    (0, 1), (-5, 1), (99999, 1440), (True, 15),
])
def test_minutes_coercion(raw, expected):
    assert call_orchestrator._coerce_minutes(raw) == expected


def test_unknown_session_is_404(client, seeded):
    for path, payload in [("/tools/schedule_callback", {"session_id": 9999}),
                          ("/tools/flag_emergency", {"session_id": "abc", "reason": "x",
                                                     "recommended_action": "ambulance"})]:
        r = client.post(path, headers=AUTH, json=payload)
        assert r.status_code == 404
        assert r.json() == {"ok": False, "error": "unknown session"}


def test_flag_emergency_ambulance(client, db, seeded):
    sid = make_session(db, seeded["maria_id"])
    r = client.post("/tools/flag_emergency", headers=AUTH, json={
        "session_id": sid, "reason": "crushing chest pain", "recommended_action": "ambulance"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    db.expire_all()
    tri = db.scalars(select(models.TriageDecision)).one()
    assert tri.source == "agent_tool" and tri.urgency == "emergency"
    assert tri.recommended_action == "ambulance" and tri.doctor_reviewed is False
    assert tri.rationale == "Flagged by the voice agent during the call: crushing chest pain"
    assert tri.patient_id == seeded["maria_id"]
    assert db.get(models.CallSession, sid).status == "escalated"


def test_flag_emergency_doctor_callback_is_urgent(client, db, seeded, monkeypatch):
    notified: list[dict] = []

    async def fake_notify(**kwargs):
        notified.append(kwargs)

    from app.services import notifications
    monkeypatch.setattr(notifications, "notify_doctor", fake_notify)
    sid = make_session(db, seeded["maria_id"])
    r = client.post("/tools/flag_emergency", headers=AUTH, json={
        "session_id": sid, "reason": "new confusion", "recommended_action": "doctor_callback"})
    assert r.status_code == 200
    db.expire_all()
    assert db.scalars(select(models.TriageDecision)).one().urgency == "urgent"
    assert len(notified) == 1 and notified[0]["urgent"] is True


def test_flag_emergency_invalid_action_is_422(client, db, seeded):
    sid = make_session(db, seeded["maria_id"])
    r = client.post("/tools/flag_emergency", headers=AUTH, json={
        "session_id": sid, "reason": "x", "recommended_action": "dispatch_helicopter"})
    assert r.status_code == 422
    db.expire_all()
    assert db.scalars(select(models.TriageDecision)).first() is None
