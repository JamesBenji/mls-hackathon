"""Doctor's personal agent: POST/GET /agent/jobs, POST /agent/jobs/{id}/start.
clinical.extract_call_job and call_orchestrator.start_call are mocked."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app import models, timeutil
from app.adapters import ollama_client
from app.schemas.clinical import CallJobDraft, CallJobExtraction
from app.services import call_orchestrator, clinical, scheduler

TEXT = "Call Maria tomorrow at 9, ask about dizziness on Lisinopril."


def _draft(scheduled_at: str | None = None) -> CallJobDraft:
    return CallJobDraft(patient_ref="Maria", situation="Post-op, new Lisinopril",
                        treatment="Lisinopril 10mg", questions=["Any dizziness?", " "],
                        scheduled_at=scheduled_at)


@pytest.fixture
def extract(monkeypatch):
    state: dict = {"result": None, "calls": []}

    async def fake(text, doctors_patients, *, now=None):
        state["calls"].append({"text": text, "patients": doctors_patients, "now": now})
        result = state["result"]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(clinical, "extract_call_job", fake)
    return state


def test_clarification(client, db, seeded, extract):
    extract["result"] = CallJobExtraction(
        clarification="Do you mean Maria Gomez or Maria Lopez?",
        candidates=[{"id": seeded["maria_id"], "name": "Maria Gomez", "score": 0.6}])
    r = client.post("/agent/jobs", json={"doctor_id": seeded["doctor_id"], "text": TEXT})
    assert r.status_code == 200
    assert r.json() == {"status": "needs_clarification",
                        "question": "Do you mean Maria Gomez or Maria Lopez?",
                        "candidates": [{"id": seeded["maria_id"], "name": "Maria Gomez",
                                        "score": 0.6}]}
    assert db.scalars(select(models.CallJob)).all() == []
    call = extract["calls"][0]
    assert call["patients"] == [{"id": seeded["maria_id"], "name": "Maria Gomez"},
                                {"id": seeded["john_id"], "name": "John Carter"}]
    assert call["now"].tzinfo is not None


def test_created_and_scheduled(client, db, seeded, extract):
    at = (timeutil.utcnow() + timedelta(days=1)).replace(microsecond=0)
    extract["result"] = CallJobExtraction(draft=_draft(), patient_id=seeded["maria_id"],
                                          scheduled_at_utc=at.isoformat())
    r = client.post("/agent/jobs", json={"doctor_id": seeded["doctor_id"], "text": TEXT,
                                         "source": "voice"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "created"
    job = body["job"]
    assert (job["status"], job["patient_id"], job["source"]) == (
        "scheduled", seeded["maria_id"], "voice")
    assert job["scheduled_at"] == at.isoformat() and job["raw_text"] == TEXT
    assert job["questions"] == ["Any dizziness?"] and job["patient_name"] == "Maria Gomez"
    assert scheduler.get_job(f"job:{job['id']}") is not None
    assert scheduler.job_run_at_utc(f"job:{job['id']}") == at


def test_created_without_time_is_pending(client, seeded, extract):
    extract["result"] = CallJobExtraction(draft=_draft(), patient_id=seeded["maria_id"])
    job = client.post("/agent/jobs", json={"doctor_id": seeded["doctor_id"],
                                           "text": TEXT}).json()["job"]
    assert job["status"] == "pending" and job["scheduled_at"] is None
    assert scheduler.get_job(f"job:{job['id']}") is None


def test_local_draft_time_fallback(client, seeded, extract):
    local = timeutil.now_local().replace(microsecond=0, tzinfo=None) + timedelta(hours=3)
    extract["result"] = CallJobExtraction(draft=_draft(local.isoformat()),
                                          patient_id=seeded["maria_id"])
    job = client.post("/agent/jobs", json={"doctor_id": seeded["doctor_id"],
                                           "text": TEXT}).json()["job"]
    assert job["status"] == "scheduled"
    assert job["scheduled_at"] == timeutil.to_utc_naive(local).isoformat()


def test_model_error_is_503(client, db, seeded, extract):
    extract["result"] = ollama_client.ModelUnavailable("connection refused")
    r = client.post("/agent/jobs", json={"doctor_id": seeded["doctor_id"], "text": TEXT})
    assert r.status_code == 503
    assert r.json() == {"detail": "model unavailable"}
    assert db.scalars(select(models.CallJob)).all() == []


def test_unknown_doctor_and_validation(client, seeded, extract):
    assert client.post("/agent/jobs", json={"doctor_id": 9999, "text": TEXT}).status_code == 404
    assert client.post("/agent/jobs", json={"doctor_id": seeded["doctor_id"],
                                            "text": ""}).status_code == 422
    assert client.post("/agent/jobs", json={"doctor_id": seeded["doctor_id"],
                                            "text": "x" * 5001}).status_code == 422
    assert extract["calls"] == []


def test_other_doctors_patient_is_never_used(client, db, seeded, extract):
    other = models.Doctor(name="Dr. Other", email="other@example.com")
    db.add(other)
    db.flush()
    stranger = models.Patient(doctor_id=other.id, name="Sam Stranger", phone="+15550000077")
    db.add(stranger)
    db.commit()
    extract["result"] = CallJobExtraction(draft=_draft(), patient_id=stranger.id)
    r = client.post("/agent/jobs", json={"doctor_id": seeded["doctor_id"], "text": TEXT})
    assert r.json()["status"] == "needs_clarification"
    assert db.scalars(select(models.CallJob)).all() == []


def test_list_jobs_with_filters(client, seeded, extract):
    extract["result"] = CallJobExtraction(draft=_draft(), patient_id=seeded["maria_id"])
    client.post("/agent/jobs", json={"doctor_id": seeded["doctor_id"], "text": TEXT})
    at = timeutil.utcnow() + timedelta(hours=5)
    extract["result"] = CallJobExtraction(draft=_draft(), patient_id=seeded["john_id"],
                                          scheduled_at_utc=at.isoformat())
    client.post("/agent/jobs", json={"doctor_id": seeded["doctor_id"], "text": TEXT})

    rows = client.get(f"/agent/jobs?doctor_id={seeded['doctor_id']}").json()
    assert [j["patient_name"] for j in rows] == ["John Carter", "Maria Gomez"]  # newest first
    assert [j["status"] for j in client.get("/agent/jobs?status=scheduled").json()] == [
        "scheduled"]
    assert client.get("/agent/jobs?doctor_id=9999").json() == []
    assert client.get("/agent/jobs?status=bogus").status_code == 422
    assert client.get(f"/agent/jobs/{rows[0]['id']}").json()["id"] == rows[0]["id"]
    assert client.get("/agent/jobs/9999").status_code == 404


def _make_job(db, seeded, **kw) -> models.CallJob:
    job = models.CallJob(doctor_id=seeded["doctor_id"], patient_id=seeded["maria_id"],
                         raw_text=TEXT, questions=["Any dizziness?"], **kw)
    db.add(job)
    db.commit()
    return job


def test_start_job(client, db, seeded, monkeypatch):
    job = _make_job(db, seeded, status="scheduled",
                    scheduled_at=timeutil.utcnow() + timedelta(hours=1))
    scheduler.schedule_start_call(job.patient_id, job.scheduled_at, call_job_id=job.id)
    seen = []

    async def fake_start(db, patient_id, *, call_job_id=None):
        seen.append((patient_id, call_job_id))
        return {"call_session_id": 7, "status": "in_progress", "question_plan": ["q"],
                "conversation_id": "mock-conv-1", "call_sid": "mock-call-1", "mock": True}

    monkeypatch.setattr(call_orchestrator, "start_call", fake_start)
    r = client.post(f"/agent/jobs/{job.id}/start")
    assert r.status_code == 200
    assert seen == [(seeded["maria_id"], job.id)]
    body = r.json()
    assert body["call"]["call_session_id"] == 7 and body["job"]["status"] == "done"
    assert scheduler.get_job(f"job:{job.id}") is None  # not called twice


@pytest.mark.parametrize(("exc", "status"), [
    (LookupError("patient not found"), 404),
    (call_orchestrator.CallStartError("ElevenLabs 500"), 502),
])
def test_start_job_errors(client, db, seeded, monkeypatch, exc, status):
    job = _make_job(db, seeded)

    async def failing(db, patient_id, *, call_job_id=None):
        raise exc

    monkeypatch.setattr(call_orchestrator, "start_call", failing)
    assert client.post(f"/agent/jobs/{job.id}/start").status_code == status
    db.expire_all()
    assert db.get(models.CallJob, job.id).status == "pending"
    assert client.post("/agent/jobs/9999/start").status_code == 404
