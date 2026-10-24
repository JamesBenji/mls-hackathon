"""POST /inbound/sms: Twilio signature, call command, time requests, red
flags, help text, unknown senders, TwiML response."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from twilio.request_validator import RequestValidator

from app import database, models, timeutil
from app.services import call_orchestrator, notifications, redflags, scheduler
from tests.conftest import TWILIO_TOKEN

URL = "https://guardian.test/inbound/sms"
MARIA_PHONE = "+15550000001"
TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response/>'


def _params(body: str, from_: str = MARIA_PHONE, sid: str = "SM0001") -> dict:
    return {"From": from_, "To": "+15559990000", "Body": body, "MessageSid": sid,
            "AccountSid": "ACtest"}


def _post(client, params: dict, signature: str | None = "valid", url: str = URL):
    headers = {}
    if signature == "valid":
        headers["X-Twilio-Signature"] = RequestValidator(TWILIO_TOKEN).compute_signature(
            url, params)
    elif signature is not None:
        headers["X-Twilio-Signature"] = signature
    return client.post("/inbound/sms", data=params, headers=headers)


def _outbound(db) -> list[models.SmsLog]:
    db.expire_all()
    return list(db.scalars(select(models.SmsLog).where(models.SmsLog.direction == "outbound")
                           .order_by(models.SmsLog.id)))


def _inbound(db) -> list[models.SmsLog]:
    db.expire_all()
    return list(db.scalars(select(models.SmsLog).where(models.SmsLog.direction == "inbound")))


@pytest.fixture
def started(monkeypatch):
    calls: list[dict] = []

    async def fake_start_call(db, patient_id, *, call_job_id=None):
        calls.append({"patient_id": patient_id, "call_job_id": call_job_id, "db": db})
        return {"call_session_id": 1, "status": "in_progress", "question_plan": [],
                "conversation_id": "mock-conv", "call_sid": "mock-call", "mock": True}

    monkeypatch.setattr(call_orchestrator, "start_call", fake_start_call)
    return calls


@pytest.fixture
def no_redflags(monkeypatch):
    monkeypatch.setattr(redflags, "scan_text", lambda text: [])


@pytest.fixture
def alerts(monkeypatch):
    sent: list[dict] = []

    async def fake_notify(**kw):
        sent.append(kw)

    monkeypatch.setattr(notifications, "notify_doctor", fake_notify)
    return sent


# --- signature -------------------------------------------------------------------

@pytest.mark.parametrize("signature", [None, "bm90LWEtc2lnbmF0dXJl"])
def test_missing_or_bad_signature_is_403(client, db, seeded, started, signature):
    r = _post(client, _params("call"), signature=signature)
    assert r.status_code == 403
    assert started == [] and _inbound(db) == [] and _outbound(db) == []


def test_signature_over_internal_url_is_rejected(client, db, seeded, started):
    # Signed for the internal URL, not PUBLIC_BASE_URL: must not validate.
    r = _post(client, _params("call"), url="http://testserver/inbound/sms")
    assert r.status_code == 403
    assert started == []


def test_tampered_body_is_rejected(client, seeded, started):
    params = _params("hello")
    sig = RequestValidator(TWILIO_TOKEN).compute_signature(URL, params)
    r = client.post("/inbound/sms", data={**params, "Body": "call"},
                    headers={"X-Twilio-Signature": sig})
    assert r.status_code == 403
    assert started == []


# --- call command ------------------------------------------------------------------

@pytest.mark.parametrize("body", ["call", "CALL", " Call me! ", "call me now", "Ruf mich an"])
def test_call_command_starts_call_and_replies(client, db, seeded, started, body):
    r = _post(client, _params(body))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert r.text == TWIML
    assert [c["patient_id"] for c in started] == [seeded["maria_id"]]
    assert started[0]["call_job_id"] is None
    replies = _outbound(db)
    assert [(s.body, s.kind, s.to_number, s.patient_id) for s in replies] == [
        ("Calling you now.", "reply", MARIA_PHONE, seeded["maria_id"])]


def test_background_call_uses_its_own_session(client, seeded, started, monkeypatch):
    created = []
    real = database.SessionLocal

    def tracking_session():
        s = real()
        created.append(s)
        return s

    monkeypatch.setattr(database, "SessionLocal", tracking_session)
    _post(client, _params("call"))
    assert len(started) == 1
    # created[0] is the request session (get_db); the task must not reuse it.
    assert len(created) == 2
    assert started[0]["db"] is created[1]


def test_failing_background_call_does_not_break_webhook(client, db, seeded, monkeypatch):
    async def boom(db, patient_id, *, call_job_id=None):
        raise call_orchestrator.CallStartError("ElevenLabs down")

    monkeypatch.setattr(call_orchestrator, "start_call", boom)
    r = _post(client, _params("call"))
    assert r.status_code == 200 and r.text == TWIML


def test_inbound_message_is_logged(client, db, seeded, started):
    _post(client, _params("call", sid="SM42"))
    [row] = _inbound(db)
    assert (row.direction, row.status, row.kind) == ("inbound", "received", "inbound")
    assert row.from_number == MARIA_PHONE and row.patient_id == seeded["maria_id"]
    assert row.body == "call" and row.provider_sid == "SM42"


def test_twilio_retry_is_processed_once(client, db, seeded, started):
    _post(client, _params("call", sid="SM7"))
    r = _post(client, _params("call", sid="SM7"))
    assert r.status_code == 200 and r.text == TWIML
    assert len(started) == 1
    assert len(_inbound(db)) == 1 and len(_outbound(db)) == 1


# --- time requests -------------------------------------------------------------------

def test_time_request_schedules_call_and_confirms(client, db, seeded, started, no_redflags):
    before = timeutil.utcnow()
    r = _post(client, _params("in 2 hours please"))
    assert r.status_code == 200 and r.text == TWIML
    assert started == []  # not now: scheduled
    job_id = f"start:{seeded['maria_id']}"
    assert scheduler.get_job(job_id) is not None
    run_at = scheduler.job_run_at_utc(job_id)
    assert before + timedelta(hours=2) - timedelta(minutes=1) <= run_at
    assert run_at <= timeutil.utcnow() + timedelta(hours=2, minutes=1)
    [reply] = _outbound(db)
    assert reply.kind == "reply"
    assert reply.body.startswith("Thanks! Guardian will call you ")
    assert timeutil.hhmm_local(run_at) in reply.body


def test_clock_time_reply_format(client, db, seeded, started, no_redflags, monkeypatch):
    now = timeutil.to_local(timeutil.utcnow()).replace(hour=10, minute=0, second=0,
                                                         microsecond=0)
    monkeypatch.setattr(timeutil, "now_local", lambda: now)
    _post(client, _params("18:00"))
    [reply] = _outbound(db)
    assert reply.body == "Thanks! Guardian will call you at 18:00."


def test_time_request_cancels_pending_resume(client, db, seeded, started, no_redflags):
    pending = models.CallSession(
        patient_id=seeded["maria_id"], direction="outbound", channel="voice",
        status="callback_pending", attempt=1,
        callback_at=timeutil.utcnow() + timedelta(minutes=10))
    db.add(pending)
    db.commit()
    scheduler.schedule_resume(pending.id, pending.callback_at)
    assert scheduler.get_job(f"resume:{pending.id}") is not None

    _post(client, _params("um 18 Uhr"))
    assert scheduler.get_job(f"resume:{pending.id}") is None
    assert scheduler.get_job(f"start:{seeded['maria_id']}") is not None
    db.expire_all()
    assert db.get(models.CallSession, pending.id).callback_at is None


# --- red flags / help ------------------------------------------------------------------

def test_urgent_text_creates_sms_triage_and_alerts_doctor(client, db, seeded, started,
                                                          alerts, monkeypatch):
    monkeypatch.setattr(redflags, "scan_text", lambda text: [
        redflags.RedFlagHit(category="chest_pain", matched="chest pain", source="text")])
    body = "I have chest pain since this morning"
    r = _post(client, _params(body))
    assert r.status_code == 200 and r.text == TWIML

    db.expire_all()
    [t] = db.scalars(select(models.TriageDecision)).all()
    assert (t.source, t.urgency, t.recommended_action) == ("sms", "urgent", "doctor_callback")
    assert t.patient_id == seeded["maria_id"] and t.doctor_reviewed is False
    assert t.rationale == f"Patient texted: {body}"
    assert t.rule_hits == ["chest_pain: chest pain"]
    s = db.get(models.CallSession, t.call_session_id)
    assert (s.direction, s.channel, s.status, s.patient_id) == (
        "inbound", "sms", "escalated", seeded["maria_id"])
    assert len(alerts) == 1 and alerts[0]["urgent"] is True
    assert alerts[0]["patient_id"] == seeded["maria_id"]
    [reply] = _outbound(db)
    assert "112" in reply.body and "911" in reply.body
    assert started == []  # nothing is dispatched or auto-called


def test_other_text_gets_help_and_no_triage(client, db, seeded, started, no_redflags, alerts):
    r = _post(client, _params("Thanks, feeling fine today"))
    assert r.status_code == 200 and r.text == TWIML
    [reply] = _outbound(db)
    assert reply.body == ("Reply CALL to be called now, or a time like 18:00 or "
                          "'in 2 hours'. In an emergency call 112 (EU) or 911 (US).")
    assert reply.kind == "reply"
    db.expire_all()
    assert db.scalars(select(models.TriageDecision)).all() == []
    assert alerts == [] and started == []


def test_scanner_failure_still_replies(client, db, seeded, started, monkeypatch):
    def broken(text):
        raise RuntimeError("scanner bug")

    monkeypatch.setattr(redflags, "scan_text", broken)
    r = _post(client, _params("hello"))
    assert r.status_code == 200
    assert len(_outbound(db)) == 1


def test_unknown_sender_logged_without_reply(client, db, seeded, started):
    r = _post(client, _params("call", from_="+15557777777"))
    assert r.status_code == 200 and r.text == TWIML
    [row] = _inbound(db)
    assert row.patient_id is None and row.from_number == "+15557777777"
    assert _outbound(db) == [] and started == []


# --- integration with the real red-flag scanner (no monkeypatch) -----------------------

@pytest.mark.parametrize(("body", "flagged"), [
    ("Ich habe starke Brustschmerzen", True),
    ("no chest pain, feeling fine", False),
])
def test_real_redflag_scanner(client, db, seeded, started, alerts, body, flagged):
    r = _post(client, _params(body))
    assert r.status_code == 200 and r.text == TWIML
    db.expire_all()
    rows = db.scalars(select(models.TriageDecision)).all()
    assert len(rows) == (1 if flagged else 0)
    if flagged:
        assert rows[0].source == "sms" and rows[0].rule_hits == ["chest_pain: brustschmerzen"]
    assert len(alerts) == (1 if flagged else 0)
    [reply] = _outbound(db)
    assert "112" in reply.body


def test_red_flag_with_time_still_flags_and_schedules(client, db, seeded, started, alerts):
    r = _post(client, _params("chest pain, call me in 2 hours"))
    assert r.status_code == 200
    db.expire_all()
    [t] = db.scalars(select(models.TriageDecision)).all()
    assert t.urgency == "urgent" and t.recommended_action == "doctor_callback"
    assert scheduler.get_job(f"start:{seeded['maria_id']}") is not None
    [reply] = _outbound(db)
    assert reply.body.startswith("Thanks! Guardian will call you ")
    assert "112" in reply.body


def test_call_command_places_real_mock_call(client, db, seeded):
    """End to end: real BackgroundTask + call_orchestrator.start_call in
    MOCK_TELEPHONY mode (no network)."""
    r = _post(client, _params("call"))
    assert r.status_code == 200 and r.text == TWIML
    db.expire_all()
    sessions = db.scalars(select(models.CallSession).where(
        models.CallSession.patient_id == seeded["maria_id"],
        models.CallSession.status == "in_progress")).all()
    assert len(sessions) == 1
    assert sessions[0].direction == "outbound"
    assert (sessions[0].conversation_id or "").startswith("mock-conv")
