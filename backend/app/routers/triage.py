"""Human-in-the-loop triage review (X-API-Key, applied in main.py).

GET  /triage                 unreviewed decisions, newest first
POST /triage/{id}/review     doctor confirms or overrides the recommendation

Every triage decision is a draft until a doctor reviews it. The system never
dispatches emergency services itself: "ambulance" is a recommendation shown
to the doctor.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models, timeutil
from app.database import get_db
from app.schemas.clinical import Action

router = APIRouter(tags=["triage"])


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def triage_out(t: models.TriageDecision, patient_name: str | None = None) -> dict:
    return {
        "id": t.id,
        "patient_id": t.patient_id,
        "patient_name": patient_name,
        "call_session_id": t.call_session_id,
        "urgency": t.urgency,
        "recommended_action": t.recommended_action,
        "rationale": t.rationale,
        "source": t.source,
        "rule_hits": list(t.rule_hits or []),
        "llm_urgency": t.llm_urgency,
        "uncertain_because": list(t.uncertain_because or []),
        "doctor_reviewed": t.doctor_reviewed,
        "reviewed_at": _iso(t.reviewed_at),
        "created_at": _iso(t.created_at),
    }


@router.get("/triage")
def list_triage(patient_id: int | None = None, db: Session = Depends(get_db)) -> list[dict]:
    """Decisions awaiting doctor review, newest first."""
    stmt = (
        select(models.TriageDecision, models.Patient.name)
        .outerjoin(models.Patient, models.Patient.id == models.TriageDecision.patient_id)
        .where(models.TriageDecision.doctor_reviewed.is_(False))
        .order_by(models.TriageDecision.created_at.desc(), models.TriageDecision.id.desc())
    )
    if patient_id is not None:
        stmt = stmt.where(models.TriageDecision.patient_id == patient_id)
    return [triage_out(t, name) for t, name in db.execute(stmt).all()]


class TriageReview(BaseModel):
    confirmed: bool = True
    override_action: Action | None = None


@router.post("/triage/{triage_id}/review")
def review_triage(triage_id: int, body: TriageReview, db: Session = Depends(get_db)) -> dict:
    t = db.get(models.TriageDecision, triage_id)
    if t is None:
        raise HTTPException(status_code=404, detail="triage decision not found")
    t.doctor_reviewed = True
    t.reviewed_at = timeutil.utcnow()
    if body.override_action:
        t.recommended_action = body.override_action
    db.commit()
    return {"id": t.id, "doctor_reviewed": True,
            "recommended_action": t.recommended_action,
            "reviewed_at": _iso(t.reviewed_at)}
