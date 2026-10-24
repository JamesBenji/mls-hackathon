"""POST /calls/webhook: signature auth, idempotency, session lookup, missed
calls. Also hosts small helpers reused by the other orchestration tests."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import models, timeutil
from app.adapters import elevenlabs
from app.config import settings
from app.services import call_orchestrator, scheduler
from tests.conftest import WEBHOOK_SECRET

GOOD_TURNS = [
    {"role": "agent", "message": "Did you take your blood pressure today?"},
    {"role": "user", "message": "Yes, it was 150 over 90."},
]
VOICEMAIL_TURNS = [
    {"role": "agent", "message": "Hello, this is Guardian calling for your check-in."},
    {"role": "user", "message": "..."},
]


# --- helpers (imported by other test modules) ---------------------------------

def sign(raw: bytes) -> str:
    try:
        return elevenlabs.sign_webhook(raw, WEBHOOK_SECRET)
    except NotImplementedError:  # adapter not implemented yet: same scheme by hand
        ts = int(time.time())
        mac = hmac.new(WEBHOOK_SECRET.encode(), f"{ts}.".encode() + raw,
                       hashlib.sha256).hexdigest()
        return f"t={ts},v0={mac}"


def post_event(client, event: dict, *, signature: str | None = "auto"):
    raw = json.dumps(event).encode()
    headers = {"Content-Type": "application/json"}
    if signature == "auto":
        headers["elevenlabs-signature"] = sign(raw)
    elif signature is not None:
        headers["elevenlabs-signature"] = signature
    return client.post("/calls/webhook", content=raw, headers=headers)


def transcription_event(conv_id: str, *, session_id: int | None = None,
                        turns: list[dict] | None = None) -> dict:
    data: dict = {
        "agent_id": "agent_test", "conversation_id": conv_id,
        "transcript": GOOD_TURNS if turns is None else turns,
        "analysis": {"transcript_summary": "Patient reported BP."},
    }
    if session_id is not None:
        data["conversation_initiation_client_data"] = {
            "dynamic_variables": {"session_id": str(session_id), "patient_name": "Maria"}}
    return {"type": "post_call_transcription", "event_timestamp": int(time.time()), "data": data}


def failure_event(conv_id: str, reason: str = "no-answer") -> dict:
    return {"type": "call_initiation_failure", "event_timestamp": int(time.time()),
            "data": {"agent_id": "agent_test", "conversation_id": conv_id,
                     "failure_reason": reason, "metadata": {"type": "twilio"}}}


def make_session(db, patient_id: int | None, *, conv_id: str | None = "conv-1",
                 status: str = "in_progress", attempt: int = 1) -> int:
    s = models.CallSession(patient_id=patient_id, direction="outbound", channel="voice",
                           status=status, attempt=attempt, conversation_id=conv_id,
                           started_at=timeutil.utcnow())
    db.add(s)
    db.flush()
    db.add(models.CallAttempt(call_session_id=s.id, attempt=attempt, conversation_id=conv_id,
                              status="placed"))
    db.commit()
    return s.id


def count(db, model, *where) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(model).where(*where))


@pytest.fixture
def jobs(monkeypatch):
    """Record handle_post_call enqueues instead of running the pipeline."""
    calls: list[int] = []

    async def fake(session_id: int, *, force: bool = False) -> None:
        calls.append(session_id)

    monkeypatch.setattr(call_orchestrator, "handle_post_call", fake)
    return calls


# --- signature -------------------------------------------------------------------

def test_missing_signature_is_rejected(client, db, seeded, jobs):
    sid = make_session(db, seeded["maria_id"])
    r = post_event(client, transcription_event("conv-1", session_id=sid), signature=None)
    assert r.status_code == 401
    assert count(db, models.Transcription) == 0 and jobs == []


@pytest.mark.parametrize("header", ["garbage", "t=1700000000,v0=deadbeef"])
def test_invalid_signature_is_rejected(client, db, seeded, jobs, header):
    sid = make_session(db, seeded["maria_id"])
    r = post_event(client, transcription_event("conv-1", session_id=sid), signature=header)
    assert r.status_code == 401
    assert count(db, models.Transcription) == 0


def test_tampered_body_is_rejected(client, db, seeded, jobs):
    sid = make_session(db, seeded["maria_id"])
    raw = json.dumps(transcription_event("conv-1", session_id=sid)).encode()
    header = sign(raw)
    r = client.post("/calls/webhook", content=raw.replace(b"150", b"190"),
                    headers={"elevenlabs-signature": header})
    assert r.status_code == 401


# --- post_call_transcription -------------------------------------------------------

def test_transcription_is_stored_session_completed_and_job_enqueued(client, db, seeded, jobs):
    sid = make_session(db, seeded["maria_id"])
    r = post_event(client, transcription_event("conv-1", session_id=sid))
    assert r.status_code == 200
    assert r.json()["status"] == "received"
    db.expire_all()
    t = db.scalars(select(models.Transcription)).one()
    assert t.call_session_id == sid and t.patient_id == seeded["maria_id"]
    assert t.source == "call" and t.conversation_id == "conv-1"
    assert t.text == ("Agent: Did you take your blood pressure today?\n"
                      "Patient: Yes, it was 150 over 90.")
    assert t.turns == GOOD_TURNS and t.analysis["transcript_summary"]
    s = db.get(models.CallSession, sid)
    assert s.status == "completed" and s.ended_at is not None
    assert db.scalars(select(models.CallAttempt)).one().status == "completed"
    assert jobs == [sid]


def test_same_webhook_twice_creates_one_transcription_and_one_job(client, db, seeded, jobs):
    sid = make_session(db, seeded["maria_id"])
    event = transcription_event("conv-1", session_id=sid)
    first = post_event(client, event)
    second = post_event(client, event)
    assert first.status_code == second.status_code == 200
    assert second.json().get("duplicate") is True
    assert count(db, models.Transcription) == 1
    assert jobs == [sid]


def test_session_recovered_via_conversation_id(client, db, seeded, jobs):
    sid = make_session(db, seeded["maria_id"], conv_id="conv-xyz")
    r = post_event(client, transcription_event("conv-xyz"))  # no dynamic variables
    assert r.status_code == 200
    db.expire_all()
    assert db.scalars(select(models.Transcription)).one().call_session_id == sid
    assert jobs == [sid]


def test_unknown_conversation_is_stored_unlinked(client, db, seeded, jobs):
    r = post_event(client, transcription_event("conv-nobody"))
    assert r.status_code == 200 and r.json()["status"] == "received"
    db.expire_all()
    assert db.scalars(select(models.Transcription)).one().call_session_id is None
    assert jobs == []


def test_escalated_session_stays_escalated(client, db, seeded, jobs):
    sid = make_session(db, seeded["maria_id"], status="escalated")
    post_event(client, transcription_event("conv-1", session_id=sid))
    db.expire_all()
    assert db.get(models.CallSession, sid).status == "escalated"
    assert jobs == [sid]


def test_callback_pending_session_stores_transcript_without_report_job(client, db, seeded, jobs):
    sid = make_session(db, seeded["maria_id"], status="callback_pending")
    r = post_event(client, transcription_event("conv-1", session_id=sid))
    assert r.status_code == 200 and r.json().get("deferred") is True
    assert count(db, models.Transcription, models.Transcription.call_session_id == sid) == 1
    db.expire_all()
    assert db.get(models.CallSession, sid).status == "callback_pending"
    assert jobs == []


def test_post_call_audio_is_logged_only(client, db, seeded, jobs):
    r = post_event(client, {"type": "post_call_audio",
                            "data": {"conversation_id": "conv-1", "full_audio": "AAAA"}})
    assert r.status_code == 200 and r.json()["status"] == "received"
    assert count(db, models.Transcription) == 0 and jobs == []


def test_persistence_failure_returns_500(client, db, seeded, monkeypatch):
    async def boom(db, event, background):
        raise RuntimeError("db down")

    monkeypatch.setattr(call_orchestrator, "process_webhook_event", boom)
    r = post_event(client, transcription_event("conv-1"))
    assert r.status_code == 500


def test_background_failure_does_not_change_the_200(client, db, seeded, monkeypatch):
    async def crash(db, session_id: int, *, force: bool) -> None:
        raise RuntimeError("model server exploded")

    monkeypatch.setattr(call_orchestrator, "_post_call", crash)
    sid = make_session(db, seeded["maria_id"])
    r = post_event(client, transcription_event("conv-1", session_id=sid))
    assert r.status_code == 200 and r.json()["status"] == "received"
    assert count(db, models.Transcription) == 1


# --- call_initiation_failure -----------------------------------------------------

def test_initiation_failure_marks_missed_sends_sms_and_schedules_resume(client, db, seeded, jobs):
    sid = make_session(db, seeded["maria_id"], conv_id="conv-miss")
    before = timeutil.utcnow()
    r = post_event(client, failure_event("conv-miss", "no-answer"))
    assert r.status_code == 200 and r.json()["status"] == "received"
    db.expire_all()
    s = db.get(models.CallSession, sid)
    assert s.status == "missed"
    attempt = db.scalars(select(models.CallAttempt)).one()
    assert attempt.status == "missed" and attempt.failure_reason == "no-answer"
    expected = before + timedelta(minutes=settings.missed_call_retry_minutes)
    assert abs((s.callback_at - expected).total_seconds()) < 60
    sms = db.scalars(select(models.SmsLog)).one()
    assert sms.kind == "missed" and sms.patient_id == seeded["maria_id"]
    assert timeutil.hhmm_local(s.callback_at) in sms.body
    assert "Reply CALL" in sms.body
    job_at = scheduler.job_run_at_utc(f"resume:{sid}")
    assert job_at is not None and abs((job_at - s.callback_at).total_seconds()) < 2


def test_initiation_failure_delivered_twice_sends_one_sms(client, db, seeded, jobs):
    make_session(db, seeded["maria_id"], conv_id="conv-miss")
    post_event(client, failure_event("conv-miss"))
    r = post_event(client, failure_event("conv-miss"))
    assert r.json().get("duplicate") is True
    assert count(db, models.SmsLog) == 1


def test_attempt_cap_gives_up_with_system_triage(client, db, seeded, jobs):
    sid = make_session(db, seeded["maria_id"], conv_id="conv-last",
                       attempt=settings.max_call_attempts)
    post_event(client, failure_event("conv-last", "busy"))
    db.expire_all()
    s = db.get(models.CallSession, sid)
    assert s.status == "failed"
    tri = db.scalars(select(models.TriageDecision)).one()
    assert tri.source == "system" and tri.urgency == "monitor"
    assert tri.recommended_action == "doctor_callback" and tri.doctor_reviewed is False
    assert f"{settings.max_call_attempts} attempts" in tri.rationale
    assert scheduler.get_job(f"resume:{sid}") is None
    sms = db.scalars(select(models.SmsLog)).one()
    assert "doctor's office will contact you" in sms.body
    assert jobs == []  # no transcripts -> nothing to report


def test_give_up_reports_a_partial_interview(client, db, seeded, jobs):
    sid = make_session(db, seeded["maria_id"], conv_id="conv-last",
                       attempt=settings.max_call_attempts)
    db.add(models.Transcription(source="call", call_session_id=sid,
                                patient_id=seeded["maria_id"], conversation_id="conv-first",
                                text="Agent: Any pain?\nPatient: A bit of a headache."))
    db.commit()
    post_event(client, failure_event("conv-last"))
    assert jobs == [sid]


# --- voicemail ---------------------------------------------------------------------

def test_voicemail_only_transcript_is_treated_as_missed(client, db, seeded):
    sid = make_session(db, seeded["maria_id"], conv_id="conv-vm")
    r = post_event(client, transcription_event("conv-vm", session_id=sid, turns=VOICEMAIL_TURNS))
    assert r.status_code == 200
    db.expire_all()
    s = db.get(models.CallSession, sid)
    assert s.status == "missed"
    assert count(db, models.CallReport, models.CallReport.call_session_id == sid) == 0
    assert db.scalars(select(models.SmsLog)).one().kind == "missed"
    assert scheduler.get_job(f"resume:{sid}") is not None
    assert count(db, models.Transcription) == 1  # transcript kept
