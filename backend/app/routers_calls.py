"""Call orchestration endpoints — channel-agnostic core logic.

POST /calls/start           build question plan -> place call -> create session
POST /calls/{id}/analyze    transcript -> MedGemma -> report + triage
GET  /calls/{id}            session + report + triage
POST /triage/{id}/review    doctor confirms/overrides the model decision
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import agent, models
from app.channels import elevenlabs_adapter
from app.database import get_db

router = APIRouter()


def _patient_dict(db: Session, patient: models.Patient) -> dict:
    meds = db.scalars(
        select(models.Medication).where(models.Medication.patient_id == patient.id)
    ).all()
    return {
        "name": patient.name,
        "category": patient.category,
        "risk_level": patient.risk_level,
        "chronic_conditions": patient.chronic_conditions,
        "medications": [{"name": m.name, "dose": m.dose} for m in meds],
    }


def _recent_reports(db: Session, patient_id: int) -> list[dict]:
    reports = db.scalars(
        select(models.CallReport)
        .where(models.CallReport.patient_id == patient_id)
        .order_by(models.CallReport.created_at.desc())
    ).all()
    return [{"summary": r.summary} for r in reports]


class StartCallBody(BaseModel):
    patient_id: int


@router.post("/calls/start")
async def start_call(body: StartCallBody, db: Session = Depends(get_db)):
    patient = db.get(models.Patient, body.patient_id)
    if not patient:
        raise HTTPException(404, "patient not found")

    template = db.scalars(
        select(models.QuestionTemplate).where(
            models.QuestionTemplate.patient_id == patient.id
        )
    ).first()
    doctor_questions = template.questions if template else [
        "How are you feeling overall today?",
    ]

    reports = _recent_reports(db, patient.id)
    plan = await agent.build_question_plan(
        _patient_dict(db, patient), doctor_questions, reports
    )

    call = await elevenlabs_adapter.start_outbound_call(
        patient.phone, plan, patient.name
    )

    session = models.CallSession(
        patient_id=patient.id, direction="outbound", channel="voice",
        status="in_progress" if call["status"] == "placed" else "scheduled",
        started_at=datetime.now(),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return {"call_session_id": session.id, "question_plan": plan, "call": call}


class AnalyzeBody(BaseModel):
    transcript: str


@router.post("/calls/{session_id}/analyze")
async def analyze_call(session_id: int, body: AnalyzeBody,
                       db: Session = Depends(get_db)):
    session = db.get(models.CallSession, session_id)
    if not session:
        raise HTTPException(404, "call session not found")
    patient = db.get(models.Patient, session.patient_id)

    reports = _recent_reports(db, patient.id)
    result = await agent.analyze_call(
        _patient_dict(db, patient), body.transcript, reports
    )

    # Persist transcript + report + triage.
    db.add(models.Transcription(
        patient_id=patient.id, source="call", text=body.transcript
    ))
    report = models.CallReport(
        call_session_id=session.id, patient_id=patient.id,
        summary=result.get("summary"), soap_note=result.get("soap_note"),
        structured_data=result.get("structured_data"),
        risk_flags=result.get("risk_flags") or [],
        escalated=bool(result.get("escalate")),
    )
    db.add(report)

    triage = models.TriageDecision(
        call_session_id=session.id, patient_id=patient.id,
        urgency=result.get("urgency", "none"),
        recommended_action=result.get("recommended_action", "none"),
        rationale=result.get("rationale"),
    )
    db.add(triage)

    # Auto-update patient vitals from any extracted values.
    sd = result.get("structured_data") or {}
    for vtype, key, unit in [
        ("bp_systolic", "bp_systolic", "mmHg"),
        ("bp_diastolic", "bp_diastolic", "mmHg"),
        ("blood_sugar", "blood_sugar", "mg/dL"),
        ("weight", "weight_kg", "kg"),
    ]:
        if sd.get(key) is not None:
            db.add(models.Vital(
                patient_id=patient.id, type=vtype, value=float(sd[key]),
                unit=unit, source="call",
            ))

    session.status = "completed"
    session.ended_at = datetime.now()
    db.commit()
    db.refresh(report)
    db.refresh(triage)
    return {
        "report_id": report.id,
        "triage": {
            "id": triage.id, "urgency": triage.urgency,
            "recommended_action": triage.recommended_action,
            "rationale": triage.rationale,
        },
        "analysis": result,
    }


@router.get("/calls/{session_id}")
def get_call(session_id: int, db: Session = Depends(get_db)):
    session = db.get(models.CallSession, session_id)
    if not session:
        raise HTTPException(404, "call session not found")
    report = db.scalars(
        select(models.CallReport).where(
            models.CallReport.call_session_id == session.id
        )
    ).first()
    triage = db.scalars(
        select(models.TriageDecision).where(
            models.TriageDecision.call_session_id == session.id
        )
    ).first()
    return {
        "id": session.id, "patient_id": session.patient_id,
        "direction": session.direction, "status": session.status,
        "report": {"summary": report.summary, "soap_note": report.soap_note,
                   "escalated": report.escalated} if report else None,
        "triage": {"urgency": triage.urgency,
                   "recommended_action": triage.recommended_action}
        if triage else None,
    }


class ReviewBody(BaseModel):
    confirmed: bool
    override_action: str | None = None


@router.post("/triage/{triage_id}/review")
def review_triage(triage_id: int, body: ReviewBody,
                  db: Session = Depends(get_db)):
    triage = db.get(models.TriageDecision, triage_id)
    if not triage:
        raise HTTPException(404, "triage decision not found")
    triage.doctor_reviewed = True
    if body.override_action:
        triage.recommended_action = body.override_action
    db.commit()
    return {"id": triage.id, "doctor_reviewed": True,
            "recommended_action": triage.recommended_action}
