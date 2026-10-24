"""Clinical service interface — the seam between telephony and the brain.

Colleague B's telephony layer (ElevenLabs/Twilio) calls these functions;
they wrap the MedGemma reasoning in app/agent.py. Keeping this interface
stable means the telephony code never imports MedGemma details directly.

Signatures match the stubs named in ELEVENLABS_TWILIO_SETUP.md section 3
(services/clinical.py: build_question_plan(), generate_report(), triage()).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import agent, models


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


def _recent_reports(db: Session, patient_id: int, limit: int = 3) -> list[dict]:
    reports = db.scalars(
        select(models.CallReport)
        .where(models.CallReport.patient_id == patient_id)
        .order_by(models.CallReport.created_at.desc())
    ).all()
    return [{"summary": r.summary} for r in reports[:limit]]


async def build_question_plan(db: Session, patient_id: int) -> list[str]:
    """Pre-call: doctor's questions + history-aware follow-ups (MedGemma)."""
    patient = db.get(models.Patient, patient_id)
    if not patient:
        raise ValueError(f"patient {patient_id} not found")

    template = db.scalars(
        select(models.QuestionTemplate).where(
            models.QuestionTemplate.patient_id == patient_id
        )
    ).first()
    doctor_questions = template.questions if template else [
        "How are you feeling overall today?",
    ]
    reports = _recent_reports(db, patient_id)
    return await agent.build_question_plan(
        _patient_dict(db, patient), doctor_questions, reports
    )


async def analyze(db: Session, patient_id: int, transcript: str) -> dict:
    """Post-call: run MedGemma over the transcript -> structured analysis.

    Returns the raw analysis dict (summary, soap_note, structured_data,
    risk_flags, urgency, recommended_action, escalate). Persistence is the
    caller's job via generate_report()/triage() below, or persist_analysis().
    """
    patient = db.get(models.Patient, patient_id)
    if not patient:
        raise ValueError(f"patient {patient_id} not found")
    reports = _recent_reports(db, patient_id)
    return await agent.analyze_call(_patient_dict(db, patient), transcript, reports)


def persist_analysis(
    db: Session, call_session_id: int, patient_id: int, transcript: str,
    analysis: dict,
) -> dict:
    """Write transcript + report + triage, and auto-update vitals.

    Returns {"report_id", "triage_id"}. Idempotency/dedupe is the telephony
    layer's responsibility (keyed on conversation_id) per the setup spec.
    """
    db.add(models.Transcription(
        patient_id=patient_id, source="call", text=transcript
    ))
    report = models.CallReport(
        call_session_id=call_session_id, patient_id=patient_id,
        summary=analysis.get("summary"), soap_note=analysis.get("soap_note"),
        structured_data=analysis.get("structured_data"),
        risk_flags=analysis.get("risk_flags") or [],
        escalated=bool(analysis.get("escalate")),
    )
    db.add(report)

    triage = models.TriageDecision(
        call_session_id=call_session_id, patient_id=patient_id,
        urgency=analysis.get("urgency", "none"),
        recommended_action=analysis.get("recommended_action", "none"),
        rationale=analysis.get("rationale"),
    )
    db.add(triage)

    sd = analysis.get("structured_data") or {}
    for vtype, key, unit in [
        ("bp_systolic", "bp_systolic", "mmHg"),
        ("bp_diastolic", "bp_diastolic", "mmHg"),
        ("blood_sugar", "blood_sugar", "mg/dL"),
        ("weight", "weight_kg", "kg"),
    ]:
        if sd.get(key) is not None:
            try:
                db.add(models.Vital(
                    patient_id=patient_id, type=vtype, value=float(sd[key]),
                    unit=unit, source="call",
                ))
            except (TypeError, ValueError):
                pass

    db.commit()
    db.refresh(report)
    db.refresh(triage)
    return {"report_id": report.id, "triage_id": triage.id}
