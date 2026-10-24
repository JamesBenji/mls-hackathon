"""FastAPI app — channel-agnostic core.

Read-only endpoints for now (patients, reports, triage, medgemma health).
Call orchestration, triage engine, and channel adapters come next.
"""
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import medgemma, models
from app.database import get_db
from app.routers_calls import router as calls_router

app = FastAPI(title="Guardian API", version="0.1.0")

# Flutter web/mobile will call this during dev; wide-open CORS for the hackathon.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(calls_router)


@app.get("/health")
async def health():
    return {"status": "ok", "medgemma": await medgemma.health()}


@app.get("/patients")
def list_patients(db: Session = Depends(get_db)):
    patients = db.scalars(select(models.Patient)).all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "phone": p.phone,
            "category": p.category,
            "risk_level": p.risk_level,
            "chronic_conditions": p.chronic_conditions,
        }
        for p in patients
    ]


@app.get("/patients/{patient_id}")
def get_patient(patient_id: int, db: Session = Depends(get_db)):
    p = db.get(models.Patient, patient_id)
    if not p:
        raise HTTPException(404, "patient not found")
    vitals = db.scalars(
        select(models.Vital).where(models.Vital.patient_id == patient_id)
    ).all()
    meds = db.scalars(
        select(models.Medication).where(models.Medication.patient_id == patient_id)
    ).all()
    reports = db.scalars(
        select(models.CallReport)
        .where(models.CallReport.patient_id == patient_id)
        .order_by(models.CallReport.created_at.desc())
    ).all()
    return {
        "id": p.id,
        "name": p.name,
        "phone": p.phone,
        "blood_type": p.blood_type,
        "height_cm": p.height_cm,
        "weight_kg": p.weight_kg,
        "chronic_conditions": p.chronic_conditions,
        "category": p.category,
        "risk_level": p.risk_level,
        "vitals": [
            {"type": v.type, "value": v.value, "unit": v.unit,
             "recorded_at": v.recorded_at.isoformat() if v.recorded_at else None}
            for v in vitals
        ],
        "medications": [
            {"name": m.name, "dose": m.dose, "schedule": m.schedule,
             "is_new_prescription": m.is_new_prescription}
            for m in meds
        ],
        "reports": [
            {"id": r.id, "summary": r.summary, "escalated": r.escalated,
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in reports
        ],
    }


@app.get("/triage")
def list_triage(db: Session = Depends(get_db)):
    """Decisions awaiting doctor review (human-in-the-loop)."""
    rows = db.scalars(
        select(models.TriageDecision).where(
            models.TriageDecision.doctor_reviewed.is_(False)
        )
    ).all()
    return [
        {
            "id": t.id,
            "patient_id": t.patient_id,
            "urgency": t.urgency,
            "recommended_action": t.recommended_action,
            "rationale": t.rationale,
        }
        for t in rows
    ]
