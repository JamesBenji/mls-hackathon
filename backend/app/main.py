"""FastAPI app — Guardian backend (clinical core + data/CRUD).

Telephony (ElevenLabs/Twilio) is owned by a separate integration and lives
in its own modules; this app exposes the clinical reasoning + data the
dashboard and telephony layer consume.
"""
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import medgemma, models
from app.database import get_db
from app.services import clinical, notify
from app.photos.routes import router as photo_router

app = FastAPI(title="Guardian API", version="0.1.0")
app.include_router(photo_router)

# Flutter web/mobile will call this during dev; wide-open CORS for the hackathon.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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


# --- Write / edit endpoints (dashboard) ---

class PatientPatch(BaseModel):
    blood_type: str | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    category: str | None = None
    risk_level: str | None = None
    chronic_conditions: list[str] | None = None


@app.patch("/patients/{patient_id}")
def edit_patient(patient_id: int, body: PatientPatch,
                 db: Session = Depends(get_db)):
    p = db.get(models.Patient, patient_id)
    if not p:
        raise HTTPException(404, "patient not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(p, field, value)
    db.commit()
    return {"id": p.id, "updated": body.model_dump(exclude_unset=True)}


class VitalIn(BaseModel):
    type: str
    value: float
    unit: str | None = None
    source: str = "manual"


@app.post("/patients/{patient_id}/vitals")
def add_vital(patient_id: int, body: VitalIn, db: Session = Depends(get_db)):
    if not db.get(models.Patient, patient_id):
        raise HTTPException(404, "patient not found")
    v = models.Vital(patient_id=patient_id, type=body.type, value=body.value,
                     unit=body.unit, source=body.source)
    db.add(v)
    db.commit()
    db.refresh(v)
    return {"id": v.id, "type": v.type, "value": v.value}


@app.get("/patients/{patient_id}/vitals")
def list_vitals(patient_id: int, db: Session = Depends(get_db)):
    rows = db.scalars(
        select(models.Vital).where(models.Vital.patient_id == patient_id)
        .order_by(models.Vital.recorded_at)
    ).all()
    return [{"type": v.type, "value": v.value, "unit": v.unit,
             "recorded_at": v.recorded_at.isoformat() if v.recorded_at else None}
            for v in rows]


class TriageReview(BaseModel):
    confirmed: bool = True
    override_action: str | None = None


@app.post("/triage/{triage_id}/review")
def review_triage(triage_id: int, body: TriageReview,
                  db: Session = Depends(get_db)):
    t = db.get(models.TriageDecision, triage_id)
    if not t:
        raise HTTPException(404, "triage decision not found")
    t.doctor_reviewed = True
    if body.override_action:
        t.recommended_action = body.override_action
    db.commit()
    return {"id": t.id, "doctor_reviewed": True,
            "recommended_action": t.recommended_action}


# --- Clinical (MedGemma) — usable directly, and by the telephony layer ---

@app.get("/clinical/{patient_id}/question-plan")
async def question_plan(patient_id: int, db: Session = Depends(get_db)):
    """Build a history-aware question plan for a patient (MedGemma)."""
    try:
        plan = await clinical.build_question_plan(db, patient_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"patient_id": patient_id, "question_plan": plan}


class AnalyzeIn(BaseModel):
    transcript: str
    call_session_id: int | None = None


@app.post("/clinical/{patient_id}/analyze")
async def analyze(patient_id: int, body: AnalyzeIn,
                  db: Session = Depends(get_db)):
    """Run MedGemma over a transcript. Persists if a call_session_id is given.

    Lets you exercise the clinical brain without the telephony layer.
    """
    try:
        result = await clinical.analyze(db, patient_id, body.transcript)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    saved = None
    if body.call_session_id is not None:
        saved = clinical.persist_analysis(
            db, body.call_session_id, patient_id, body.transcript, result
        )
    return {"analysis": result, "saved": saved}


# --- Reports: detail, PDF download, doctor notification ---

@app.get("/reports/{report_id}")
def get_report(report_id: int, db: Session = Depends(get_db)):
    r = db.get(models.CallReport, report_id)
    if not r:
        raise HTTPException(404, "report not found")
    return {
        "id": r.id, "patient_id": r.patient_id,
        "summary": r.summary, "soap_note": r.soap_note,
        "structured_data": r.structured_data, "risk_flags": r.risk_flags,
        "escalated": r.escalated, "pdf_path": r.pdf_path,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@app.get("/reports/{report_id}/pdf")
async def download_report_pdf(report_id: int, db: Session = Depends(get_db)):
    r = db.get(models.CallReport, report_id)
    if not r:
        raise HTTPException(404, "report not found")
    # Generate on demand if not already rendered.
    if not r.pdf_path:
        await notify.notify_doctor(db, report_id)
        db.refresh(r)
    return FileResponse(r.pdf_path, media_type="application/pdf",
                        filename=f"guardian_report_{report_id}.pdf")


@app.post("/reports/{report_id}/notify")
async def notify_report(report_id: int, db: Session = Depends(get_db)):
    """Generate PDF + email the doctor (summary + dashboard link + PDF)."""
    try:
        return await notify.notify_doctor(db, report_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
