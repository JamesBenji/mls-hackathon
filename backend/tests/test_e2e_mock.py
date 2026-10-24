"""End-to-end with MOCK_TELEPHONY=true (ELEVENLABS_TWILIO_SETUP.md step 8):
start call -> signed post-call webhook -> report job ran; plus the
"call me back" flow spanning two conversations with ONE report."""
from __future__ import annotations

from sqlalchemy import select

from app import models
from app.config import settings
from app.services import scheduler
from tests.conftest import TOOL_TOKEN
from tests.test_call_start import capture_calls
from tests.test_post_call_pipeline import fake_clinical  # noqa: F401  (fixture)
from tests.test_webhooks import count, post_event, transcription_event

AUTH = {"Authorization": f"Bearer {TOOL_TOKEN}"}


def test_start_call_then_webhook_builds_the_report(client, db, seeded, fake_clinical):  # noqa: F811
    assert settings.mock_telephony is True
    start = client.post("/calls/start", json={"patient_id": seeded["maria_id"]}).json()
    sid, conv = start["call_session_id"], start["conversation_id"]

    r = post_event(client, transcription_event(conv, session_id=sid))
    assert r.status_code == 200 and r.json()["status"] == "received"

    detail = client.get(f"/calls/{sid}").json()
    assert detail["status"] == "completed"
    assert detail["attempts"][0]["status"] == "completed"
    assert detail["report"]["status"] == "ok"
    assert detail["triage"][0]["source"] == "pipeline"
    assert len(fake_clinical.report_calls) == 1


async def test_callback_flow_one_report_over_both_conversations(client, db, seeded,
                                                                fake_clinical,  # noqa: F811
                                                                monkeypatch):
    seen = capture_calls(monkeypatch)
    start = client.post("/calls/start", json={"patient_id": seeded["maria_id"]}).json()
    sid, conv1 = start["call_session_id"], start["conversation_id"]

    r = client.post("/tools/schedule_callback", headers=AUTH, json={
        "session_id": sid, "minutes": 1, "missing_item": "blood pressure",
        "checkpoint": "Meds taken at 8am. BP not measured yet."})
    assert r.status_code == 200

    first = [{"role": "agent", "message": "Did you take your medication?"},
             {"role": "user", "message": "Yes at 8am. I haven't measured my blood pressure."}]
    r = post_event(client, transcription_event(conv1, session_id=sid, turns=first))
    assert r.json().get("deferred") is True
    assert fake_clinical.report_calls == []
    assert scheduler.get_job(f"resume:{sid}") is not None

    # The scheduler job body (what APScheduler runs at callback_at).
    await scheduler.run_resume_call(sid)
    db.expire_all()
    s = db.get(models.CallSession, sid)
    assert s.status == "in_progress" and s.attempt == 2 and s.callback_at is None
    conv2 = s.conversation_id
    assert conv2 and conv2 != conv1
    assert seen[-1][1]["resume_from"] == "Meds taken at 8am. BP not measured yet."
    attempts = db.scalars(select(models.CallAttempt).where(
        models.CallAttempt.call_session_id == sid).order_by(models.CallAttempt.attempt)).all()
    assert [(a.attempt, a.status) for a in attempts] == [(1, "completed"), (2, "mock")]

    second = [{"role": "agent", "message": "Welcome back. What is your blood pressure?"},
              {"role": "user", "message": "It is 150 over 92 now."}]
    r = post_event(client, transcription_event(conv2, session_id=sid, turns=second))
    assert r.status_code == 200 and not r.json().get("deferred")

    assert len(fake_clinical.report_calls) == 1
    text = fake_clinical.report_calls[0]["transcript"]
    assert "haven't measured my blood pressure" in text and "150 over 92" in text
    assert count(db, models.CallReport, models.CallReport.call_session_id == sid) == 1
    assert count(db, models.Transcription, models.Transcription.call_session_id == sid) == 2
    db.expire_all()
    assert db.get(models.CallSession, sid).status == "completed"
