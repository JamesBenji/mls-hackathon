"""Doctor's personal agent (X-API-Key, applied in main.py).

POST /agent/jobs              typed or speech-to-text instruction from the
                              doctor -> CallJob (or a clarification question)
GET  /agent/jobs              list (?doctor_id=, ?status=pending|scheduled|done)
GET  /agent/jobs/{id}         one job
POST /agent/jobs/{id}/start   place the job's call now

Extraction is clinical.extract_call_job (Gemma 4, MEDGEMMA_DO_SETUP.md §6.5).
The patient is only ever resolved among the requesting doctor's own
patients; an ambiguous or unknown reference returns a question instead of a
guess. Times are resolved against the current Europe/Berlin time passed in
explicitly.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models, timeutil
from app.adapters import ollama_client
from app.database import get_db
from app.schemas.clinical import CallJobExtraction
from app.services import call_orchestrator, clinical, scheduler

log = logging.getLogger(__name__)

router = APIRouter(tags=["agent"])

JobStatus = Literal["pending", "scheduled", "done"]


class AgentJobIn(BaseModel):
    doctor_id: int
    text: str = Field(min_length=1, max_length=5000)
    source: Literal["typed", "voice"] = "typed"


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def job_out(job: models.CallJob, patient_name: str | None = None) -> dict:
    return {
        "id": job.id,
        "doctor_id": job.doctor_id,
        "patient_id": job.patient_id,
        "patient_name": patient_name,
        "source": job.source,
        "raw_text": job.raw_text,
        "situation": job.situation,
        "treatment": job.treatment,
        "questions": list(job.questions or []),
        "scheduled_at": _iso(job.scheduled_at),
        "status": job.status,
        "created_at": _iso(job.created_at),
    }


def _parse_scheduled(extraction: CallJobExtraction) -> datetime | None:
    """Naive-UTC run time from the extraction (validated UTC field first)."""
    raw_utc = extraction.scheduled_at_utc
    raw_local = extraction.draft.scheduled_at if extraction.draft else None
    for raw, naive_is_utc in ((raw_utc, True), (raw_local, False)):
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            log.warning("agent job: unparseable scheduled time %r", raw)
            continue
        if dt.tzinfo is not None:
            return dt.astimezone(UTC).replace(tzinfo=None)
        return dt if naive_is_utc else timeutil.to_utc_naive(dt)  # draft is Berlin time
    return None


def _clarify(question: str, candidates: list[dict]) -> dict:
    return {"status": "needs_clarification", "question": question, "candidates": candidates}


@router.post("/agent/jobs")
async def create_job(body: AgentJobIn, db: Session = Depends(get_db)) -> dict:
    if db.get(models.Doctor, body.doctor_id) is None:
        raise HTTPException(status_code=404, detail="doctor not found")
    patients = [
        {"id": p.id, "name": p.name}
        for p in db.scalars(select(models.Patient)
                            .where(models.Patient.doctor_id == body.doctor_id)
                            .order_by(models.Patient.id))
    ]
    try:
        extraction = await clinical.extract_call_job(body.text, patients,
                                                     now=timeutil.now_local())
    except ollama_client.ModelError as exc:
        log.warning("agent job extraction failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="model unavailable") from exc

    if extraction.clarification:
        return _clarify(extraction.clarification, extraction.candidates)
    names = {p["id"]: p["name"] for p in patients}
    if extraction.draft is None or extraction.patient_id not in names:
        # Never create a job for a patient outside this doctor's list.
        return _clarify("Which patient do you mean?",
                        [{"id": pid, "name": name} for pid, name in names.items()])

    draft = extraction.draft
    at = _parse_scheduled(extraction)
    in_future = at is not None and at > timeutil.utcnow()
    job = models.CallJob(
        doctor_id=body.doctor_id, patient_id=extraction.patient_id, source=body.source,
        raw_text=body.text, situation=draft.situation, treatment=draft.treatment,
        questions=[q.strip() for q in draft.questions if q and q.strip()],
        scheduled_at=at, status="scheduled" if in_future else "pending",
    )
    db.add(job)
    db.commit()
    if in_future:
        try:
            scheduler.schedule_start_call(job.patient_id, at, call_job_id=job.id)
        except Exception:
            log.exception("could not schedule call job %s; left pending", job.id)
            job.status = "pending"
            db.commit()
    return {"status": "created", "job": job_out(job, names[job.patient_id])}


@router.get("/agent/jobs")
def list_jobs(doctor_id: int | None = None, status: JobStatus | None = None,
              db: Session = Depends(get_db)) -> list[dict]:
    stmt = (select(models.CallJob, models.Patient.name)
            .join(models.Patient, models.Patient.id == models.CallJob.patient_id)
            .order_by(models.CallJob.created_at.desc(), models.CallJob.id.desc()))
    if doctor_id is not None:
        stmt = stmt.where(models.CallJob.doctor_id == doctor_id)
    if status is not None:
        stmt = stmt.where(models.CallJob.status == status)
    return [job_out(job, name) for job, name in db.execute(stmt).all()]


def _get_job(db: Session, job_id: int) -> models.CallJob:
    job = db.get(models.CallJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="call job not found")
    return job


@router.get("/agent/jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)) -> dict:
    job = _get_job(db, job_id)
    patient = db.get(models.Patient, job.patient_id)
    return job_out(job, patient.name if patient else None)


@router.post("/agent/jobs/{job_id}/start")
async def start_job(job_id: int, db: Session = Depends(get_db)) -> dict:
    job = _get_job(db, job_id)
    try:
        result = await call_orchestrator.start_call(db, job.patient_id, call_job_id=job.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc) or "patient not found") from exc
    except call_orchestrator.CallStartError as exc:
        raise HTTPException(status_code=502, detail=f"call could not be placed: {exc}") from exc
    # Started by hand: drop the scheduled run so the patient is not called twice.
    scheduler.cancel(f"job:{job.id}")
    job.status = "done"
    db.commit()
    patient = db.get(models.Patient, job.patient_id)
    return {"job": job_out(job, patient.name if patient else None), "call": result}
