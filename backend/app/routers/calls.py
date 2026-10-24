"""Call routes (X-API-Key, added in main.py).

POST /calls/start             {patient_id, call_job_id?} -> start_call result
GET  /calls/{id}              session + attempts + transcripts + latest report + triage
POST /calls/{id}/callback     {minutes?, missing_item?, checkpoint?}
POST /calls/{id}/analyze      {transcript} -> manual transcript through the
                              same post-call pipeline, returns GET /calls/{id}
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models, timeutil
from app.database import get_db
from app.services import call_orchestrator

router = APIRouter(prefix="/calls", tags=["calls"])


class StartCallIn(BaseModel):
    patient_id: int
    call_job_id: int | None = None


class CallbackIn(BaseModel):
    minutes: int | float | str | None = None
    missing_item: str | None = None
    checkpoint: str | None = None


class AnalyzeIn(BaseModel):
    transcript: str = Field(min_length=1)


def _report_dict(r: models.CallReport) -> dict[str, Any]:
    return {
        "id": r.id, "status": r.status, "error": r.error, "summary": r.summary,
        "soap": r.soap, "soap_note": r.soap_note, "captured": r.structured_data,
        "risk_flags": list(r.risk_flags or []),
        "changes_since_last_call": list(r.changes_since_last_call or []),
        "clinical_analysis": r.clinical_analysis,
        "proposed_risk_level": r.proposed_risk_level, "model_info": r.model_info,
        "escalated": bool(r.escalated), "created_at": r.created_at,
    }


def session_detail(db: Session, session_id: int) -> dict[str, Any]:
    s = db.get(models.CallSession, session_id)
    if s is None:
        raise HTTPException(status_code=404, detail="call session not found")
    attempts = db.scalars(select(models.CallAttempt)
                          .where(models.CallAttempt.call_session_id == s.id)
                          .order_by(models.CallAttempt.attempt, models.CallAttempt.id)).all()
    texts = db.scalars(select(models.Transcription)
                       .where(models.Transcription.call_session_id == s.id)
                       .order_by(models.Transcription.created_at,
                                 models.Transcription.id)).all()
    report = db.scalars(select(models.CallReport)
                        .where(models.CallReport.call_session_id == s.id)
                        .order_by(models.CallReport.created_at.desc(),
                                  models.CallReport.id.desc())).first()
    triage = db.scalars(select(models.TriageDecision)
                        .where(models.TriageDecision.call_session_id == s.id)
                        .order_by(models.TriageDecision.created_at,
                                  models.TriageDecision.id)).all()
    return {
        "id": s.id, "patient_id": s.patient_id, "direction": s.direction,
        "channel": s.channel, "status": s.status, "attempt": s.attempt,
        "started_at": s.started_at, "ended_at": s.ended_at, "callback_at": s.callback_at,
        "checkpoint": s.checkpoint, "conversation_id": s.conversation_id,
        "call_sid": s.call_sid, "caller_number": s.caller_number,
        "call_job_id": s.call_job_id, "last_error": s.last_error, "created_at": s.created_at,
        "attempts": [{
            "id": a.id, "attempt": a.attempt, "conversation_id": a.conversation_id,
            "call_sid": a.call_sid, "resume_from": a.resume_from, "status": a.status,
            "failure_reason": a.failure_reason, "created_at": a.created_at,
            "ended_at": a.ended_at,
        } for a in attempts],
        "transcripts": [{
            "id": t.id, "source": t.source, "text": t.text,
            "conversation_id": t.conversation_id, "created_at": t.created_at,
        } for t in texts],
        "report": _report_dict(report) if report is not None else None,
        "triage": [{
            "id": d.id, "source": d.source, "urgency": d.urgency,
            "recommended_action": d.recommended_action, "rationale": d.rationale,
            "rule_hits": list(d.rule_hits or []), "llm_urgency": d.llm_urgency,
            "uncertain_because": list(d.uncertain_because or []),
            "doctor_reviewed": bool(d.doctor_reviewed), "created_at": d.created_at,
        } for d in triage],
    }


@router.post("/start")
async def start_call(body: StartCallIn, db: Session = Depends(get_db)) -> dict:
    try:
        return await call_orchestrator.start_call(db, body.patient_id,
                                                  call_job_id=body.call_job_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except call_orchestrator.CallStartError as exc:
        raise HTTPException(status_code=502, detail=f"could not place the call: {exc}") from None


@router.get("/{session_id}")
def get_call(session_id: int, db: Session = Depends(get_db)) -> dict:
    return session_detail(db, session_id)


@router.post("/{session_id}/callback")
async def schedule_callback(session_id: int, body: CallbackIn,
                            db: Session = Depends(get_db)) -> dict:
    session = db.get(models.CallSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="call session not found")
    try:
        callback_at = await call_orchestrator.schedule_callback(
            db, session, minutes=body.minutes, missing_item=body.missing_item,
            checkpoint=body.checkpoint)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {"ok": True, "callback_at": callback_at.isoformat(),
            "callback_time": timeutil.hhmm_local(callback_at)}


@router.post("/{session_id}/analyze")
async def analyze_call(session_id: int, body: AnalyzeIn, db: Session = Depends(get_db)) -> dict:
    session = db.get(models.CallSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="call session not found")
    db.add(models.Transcription(source="manual", text=body.transcript.strip(),
                                call_session_id=session.id, patient_id=session.patient_id))
    db.commit()
    await call_orchestrator.handle_post_call(session.id, force=True)
    db.expire_all()  # the pipeline wrote through its own session
    return session_detail(db, session.id)
