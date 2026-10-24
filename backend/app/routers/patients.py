"""Dashboard data routes (X-API-Key, applied in main.py).

GET    /patients                 list (optional ?category= & ?risk_level=)
GET    /patients/{id}            detail: demographics, vitals, medications,
                                 reports, upcoming appointments, the latest
                                 proposed risk level (never auto-applied)
PATCH  /patients/{id}            edit demographics / category / risk / phone
GET    /patients/{id}/vitals     time series, oldest first (optional ?type=)
POST   /patients/{id}/vitals     add a reading
GET    /patients/{id}/reports    full call reports (agent memory), newest first

The dashboard (dashboard/lib/main.dart) reads GET /patients and
GET /patients/{id}: keep those fields stable (adding fields is fine).
Datetimes are naive-UTC ISO strings (DB convention, app/timeutil.py).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models, timeutil
from app.database import get_db
from app.phone import normalize_e164

router = APIRouter(tags=["patients"])

RiskLevel = Literal["low", "medium", "high"]
VitalType = Literal["bp_systolic", "bp_diastolic", "blood_sugar", "weight", "sleep_hours"]
DEFAULT_UNITS: dict[str, str] = {
    "bp_systolic": "mmHg", "bp_diastolic": "mmHg", "blood_sugar": "mg/dL",
    "weight": "kg", "sleep_hours": "h",
}


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _get_patient(db: Session, patient_id: int) -> models.Patient:
    p = db.get(models.Patient, patient_id)
    if p is None:
        raise HTTPException(status_code=404, detail="patient not found")
    return p


def _vital_out(v: models.Vital) -> dict:
    return {"id": v.id, "type": v.type, "value": v.value, "unit": v.unit,
            "recorded_at": _iso(v.recorded_at), "source": v.source}


def _report_brief(r: models.CallReport) -> dict:
    return {"id": r.id, "summary": r.summary, "escalated": r.escalated,
            "created_at": _iso(r.created_at), "status": r.status,
            "call_session_id": r.call_session_id, "risk_flags": list(r.risk_flags or []),
            "proposed_risk_level": r.proposed_risk_level}


def _report_full(r: models.CallReport) -> dict:
    return {
        **_report_brief(r),
        "error": r.error,
        "soap": r.soap,
        "soap_note": r.soap_note,
        "structured_data": r.structured_data,
        "changes_since_last_call": list(r.changes_since_last_call or []),
        "clinical_analysis": r.clinical_analysis,
        "model_info": r.model_info,
        "pdf_path": r.pdf_path,
    }


def _reports(db: Session, patient_id: int) -> list[models.CallReport]:
    return list(db.scalars(
        select(models.CallReport)
        .where(models.CallReport.patient_id == patient_id)
        .order_by(models.CallReport.created_at.desc(), models.CallReport.id.desc())
    ))


@router.get("/patients")
def list_patients(category: str | None = None, risk_level: RiskLevel | None = None,
                  db: Session = Depends(get_db)) -> list[dict]:
    stmt = select(models.Patient).order_by(models.Patient.id)
    if category:
        stmt = stmt.where(models.Patient.category == category)
    if risk_level:
        stmt = stmt.where(models.Patient.risk_level == risk_level)
    return [
        {"id": p.id, "name": p.name, "phone": p.phone, "category": p.category,
         "risk_level": p.risk_level, "chronic_conditions": list(p.chronic_conditions or [])}
        for p in db.scalars(stmt)
    ]


@router.get("/patients/{patient_id}")
def get_patient(patient_id: int, db: Session = Depends(get_db)) -> dict:
    p = _get_patient(db, patient_id)
    vitals = db.scalars(
        select(models.Vital).where(models.Vital.patient_id == patient_id)
        .order_by(models.Vital.recorded_at, models.Vital.id)
    )
    meds = db.scalars(
        select(models.Medication).where(models.Medication.patient_id == patient_id)
        .order_by(models.Medication.id)
    )
    reports = _reports(db, patient_id)
    appointments = db.scalars(
        select(models.Appointment)
        .where(models.Appointment.patient_id == patient_id,
               models.Appointment.scheduled_at >= timeutil.utcnow())
        .order_by(models.Appointment.scheduled_at)
    )
    return {
        "id": p.id,
        "name": p.name,
        "phone": p.phone,
        "dob": p.dob.isoformat() if p.dob else None,
        "doctor_id": p.doctor_id,
        "blood_type": p.blood_type,
        "height_cm": p.height_cm,
        "weight_kg": p.weight_kg,
        "chronic_conditions": list(p.chronic_conditions or []),
        "category": p.category,
        "risk_level": p.risk_level,
        # Latest report's suggestion; the doctor applies it via PATCH.
        "proposed_risk_level": reports[0].proposed_risk_level if reports else None,
        "vitals": [_vital_out(v) for v in vitals],
        "medications": [
            {"name": m.name, "dose": m.dose, "schedule": m.schedule,
             "is_new_prescription": m.is_new_prescription,
             "started_at": m.started_at.isoformat() if m.started_at else None}
            for m in meds
        ],
        "reports": [_report_brief(r) for r in reports],
        "appointments": [
            {"id": a.id, "scheduled_at": _iso(a.scheduled_at), "type": a.type, "notes": a.notes}
            for a in appointments
        ],
    }


class PatientPatch(BaseModel):
    blood_type: str | None = Field(None, max_length=8)
    height_cm: float | None = Field(None, gt=0, lt=300)
    weight_kg: float | None = Field(None, gt=0, lt=700)
    category: str | None = Field(None, max_length=64)
    risk_level: RiskLevel | None = None
    chronic_conditions: list[str] | None = None
    phone: str | None = None

    @field_validator("phone")
    @classmethod
    def _phone_e164(cls, v: str | None) -> str | None:
        if v is None:
            return None
        normalized = normalize_e164(v)
        if normalized is None:
            raise ValueError("phone must be E.164 with country code, e.g. +4915112345678")
        return normalized

    @field_validator("chronic_conditions")
    @classmethod
    def _clean_conditions(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else [c.strip() for c in v if c and c.strip()]


_NOT_NULLABLE = ("risk_level", "phone", "chronic_conditions")


@router.patch("/patients/{patient_id}")
def edit_patient(patient_id: int, body: PatientPatch, db: Session = Depends(get_db)) -> dict:
    p = _get_patient(db, patient_id)
    updates = body.model_dump(exclude_unset=True)
    for key in _NOT_NULLABLE:
        if key in updates and updates[key] is None:
            raise HTTPException(status_code=422, detail=f"{key} cannot be null")
    if "phone" in updates and updates["phone"] != p.phone:
        # Inbound SMS/voice identify the patient by phone: keep it unique.
        clash = db.scalars(select(models.Patient.id).where(
            models.Patient.phone == updates["phone"], models.Patient.id != p.id)).first()
        if clash is not None:
            raise HTTPException(status_code=409, detail="phone already belongs to another patient")
    for field, value in updates.items():
        setattr(p, field, value)
    db.commit()
    return {"id": p.id, "updated": updates}


class VitalIn(BaseModel):
    type: VitalType
    value: float = Field(ge=0, le=1000, allow_inf_nan=False)
    unit: str | None = Field(None, max_length=16)
    source: Literal["manual", "call", "sms"] = "manual"
    # Aware datetimes are converted; naive ones are taken as UTC (as returned
    # by this API). Default: now.
    recorded_at: datetime | None = None


@router.get("/patients/{patient_id}/vitals")
def list_vitals(patient_id: int, type: VitalType | None = None,
                db: Session = Depends(get_db)) -> list[dict]:
    _get_patient(db, patient_id)
    stmt = (select(models.Vital).where(models.Vital.patient_id == patient_id)
            .order_by(models.Vital.recorded_at, models.Vital.id))
    if type:
        stmt = stmt.where(models.Vital.type == type)
    return [_vital_out(v) for v in db.scalars(stmt)]


@router.post("/patients/{patient_id}/vitals")
def add_vital(patient_id: int, body: VitalIn, db: Session = Depends(get_db)) -> dict:
    _get_patient(db, patient_id)
    recorded = body.recorded_at
    if recorded is None:
        recorded = timeutil.utcnow()
    elif recorded.tzinfo is not None:
        recorded = timeutil.to_utc_naive(recorded)
    v = models.Vital(patient_id=patient_id, type=body.type, value=body.value,
                     unit=body.unit or DEFAULT_UNITS.get(body.type), source=body.source,
                     recorded_at=recorded)
    db.add(v)
    db.commit()
    return _vital_out(v)


@router.get("/patients/{patient_id}/reports")
def list_reports(patient_id: int, db: Session = Depends(get_db)) -> list[dict]:
    _get_patient(db, patient_id)
    return [_report_full(r) for r in _reports(db, patient_id)]
