"""Internal SMS sending (X-API-Key, applied in main.py).

POST /sms/send {patient_id?, to?, body, kind} -> {id, status, provider_sid, error}

Reminders, tips, callback / missed-call notices and photo links. The
destination is `to` if given, otherwise the patient's phone. Delivery goes
through app/adapters/twilio_sms.send_sms, which always writes an sms_log row
and never raises on delivery failure (status=failed + error instead).
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import models
from app.adapters import twilio_sms
from app.database import get_db
from app.phone import normalize_e164

router = APIRouter(tags=["sms"])

SmsKind = Literal["reminder", "tip", "callback", "missed", "photo_link", "other"]


class SmsSendIn(BaseModel):
    patient_id: int | None = None
    to: str | None = None
    body: str = Field(min_length=1, max_length=1600)
    kind: SmsKind = "other"


@router.post("/sms/send")
async def send_sms(req: SmsSendIn, db: Session = Depends(get_db)) -> dict:
    patient: models.Patient | None = None
    if req.patient_id is not None:
        patient = db.get(models.Patient, req.patient_id)
        if patient is None:
            raise HTTPException(status_code=404, detail="patient not found")
    if req.to:
        to = normalize_e164(req.to)
        if to is None:
            raise HTTPException(status_code=422,
                                detail="`to` must be an E.164 number, e.g. +4915112345678")
    elif patient is not None:
        to = patient.phone
    else:
        raise HTTPException(status_code=422, detail="patient_id or to is required")
    if not req.body.strip():
        raise HTTPException(status_code=422, detail="body must not be blank")

    row = await twilio_sms.send_sms(db, to, req.body, kind=req.kind,
                                    patient_id=patient.id if patient else None)
    return {"id": row.id, "status": row.status, "provider_sid": row.provider_sid,
            "error": row.error}
