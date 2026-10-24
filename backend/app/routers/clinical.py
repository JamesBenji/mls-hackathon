"""Direct clinical-brain endpoints for testing without telephony (X-API-Key).

GET  /clinical/{patient_id}/question-plan
     -> {"patient_id", "question_plan": [texts], "plan": QuestionPlan, "rendered": str}
POST /clinical/{patient_id}/analyze {transcript, call_session_id?}
     -> {"analysis": {"report": draft | null, "report_error": str | null,
                      "triage": {...}}, "saved": {"report_id", "triage_id"} | null}
     Persists (same helper as the post-call pipeline) only when
     call_session_id is given. Everything returned is a draft for the doctor.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.services import call_orchestrator

router = APIRouter(prefix="/clinical", tags=["clinical"])


class AnalyzeIn(BaseModel):
    transcript: str = Field(min_length=1)
    call_session_id: int | None = None


def _require_patient(db: Session, patient_id: int) -> None:
    if db.get(models.Patient, patient_id) is None:
        raise HTTPException(status_code=404, detail=f"patient {patient_id} not found")


@router.get("/{patient_id}/question-plan")
async def question_plan(patient_id: int, db: Session = Depends(get_db)) -> dict:
    _require_patient(db, patient_id)
    plan = await call_orchestrator.question_plan_for(db, patient_id)
    return {
        "patient_id": patient_id,
        "question_plan": [q.text for q in plan.questions],
        "plan": plan.model_dump(),
        "rendered": call_orchestrator.render_plan(plan),
    }


@router.post("/{patient_id}/analyze")
async def analyze(patient_id: int, body: AnalyzeIn, db: Session = Depends(get_db)) -> dict:
    _require_patient(db, patient_id)
    if body.call_session_id is not None:
        session = db.get(models.CallSession, body.call_session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="call session not found")
        if session.patient_id not in (None, patient_id):
            raise HTTPException(status_code=409,
                                detail="call session belongs to a different patient")
    result = await call_orchestrator.analyze_transcript(
        db, patient_id, body.transcript, call_session_id=body.call_session_id,
        notify=body.call_session_id is not None)
    return {
        "analysis": {"report": result["report"], "report_error": result["report_error"],
                     "triage": result["triage"]},
        "saved": result["saved"],
    }
