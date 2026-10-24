"""POST /calls/start and GET /calls/{id}."""
from __future__ import annotations

from sqlalchemy import select

from app import models
from app.adapters import elevenlabs
from app.services import call_orchestrator


def capture_calls(monkeypatch) -> list[tuple[str, dict]]:
    """Wrap the real (mock-mode) adapter so the dynamic variables are visible."""
    seen: list[tuple[str, dict]] = []
    real = elevenlabs.place_outbound_call

    async def recording(to_number: str, dynamic_variables: dict) -> dict:
        seen.append((to_number, dict(dynamic_variables)))
        return await real(to_number, dynamic_variables)

    monkeypatch.setattr(elevenlabs, "place_outbound_call", recording)
    return seen


def test_start_call_in_mock_mode(client, db, seeded, monkeypatch):
    seen = capture_calls(monkeypatch)
    r = client.post("/calls/start", json={"patient_id": seeded["maria_id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mock"] is True and body["status"] == "in_progress"
    assert body["conversation_id"] and body["call_sid"]
    assert "Did you take your blood pressure today?" in body["question_plan"]

    to, dyn = seen[0]
    assert to == "+15550000001"
    assert set(dyn) == set(elevenlabs.REQUIRED_DYNAMIC_VARIABLES)
    assert all(isinstance(v, str) for v in dyn.values())
    assert dyn["patient_name"] == "Maria Gomez"
    assert dyn["session_id"] == str(body["call_session_id"])
    assert dyn["resume_from"] == "start"
    assert "BP high" in dyn["last_call_summary"]

    db.expire_all()
    s = db.get(models.CallSession, body["call_session_id"])
    assert (s.status, s.direction, s.channel, s.attempt) == ("in_progress", "outbound",
                                                             "voice", 1)
    assert s.conversation_id == body["conversation_id"] and s.started_at is not None
    attempt = db.scalars(select(models.CallAttempt)
                         .where(models.CallAttempt.call_session_id == s.id)).one()
    assert attempt.status == "mock" and attempt.resume_from == "start"
    assert attempt.conversation_id == body["conversation_id"]

    detail = client.get(f"/calls/{s.id}").json()
    assert detail["status"] == "in_progress" and len(detail["attempts"]) == 1
    assert detail["report"] is None and detail["transcripts"] == []


def test_elevenlabs_error_is_502_and_session_failed(client, db, seeded, monkeypatch):
    async def fail(to_number, dynamic_variables):
        raise elevenlabs.ElevenLabsError("success=false: number not verified")

    monkeypatch.setattr(elevenlabs, "place_outbound_call", fail)
    r = client.post("/calls/start", json={"patient_id": seeded["maria_id"]})
    assert r.status_code == 502
    db.expire_all()
    s = db.scalars(select(models.CallSession)
                   .where(models.CallSession.status == "failed")).one()
    assert "number not verified" in s.last_error
    assert db.scalars(select(models.CallAttempt)
                      .where(models.CallAttempt.call_session_id == s.id)).first() is None


def test_unknown_patient_is_404(client, seeded):
    assert client.post("/calls/start", json={"patient_id": 9999}).status_code == 404
    assert client.get("/calls/9999").status_code == 404


def test_call_job_questions_and_status(client, db, seeded, monkeypatch):
    seen = capture_calls(monkeypatch)
    job = models.CallJob(doctor_id=seeded["doctor_id"], patient_id=seeded["john_id"],
                         questions=["Is the swelling in your ankle better?"])
    db.add(job)
    db.commit()
    r = client.post("/calls/start", json={"patient_id": seeded["john_id"],
                                          "call_job_id": job.id})
    assert r.status_code == 200
    assert r.json()["question_plan"][0] == "Is the swelling in your ankle better?"
    assert "ankle" in seen[0][1]["question_plan"]
    db.expire_all()
    assert db.get(models.CallJob, job.id).status == "done"
    s = db.get(models.CallSession, r.json()["call_session_id"])
    assert s.call_job_id == job.id


async def test_start_call_function_contract(db, seeded):
    result = await call_orchestrator.start_call(db, seeded["john_id"])
    assert set(result) == {"call_session_id", "status", "question_plan", "conversation_id",
                           "call_sid", "mock"}
    assert result["conversation_id"].startswith("mock-conv-")
