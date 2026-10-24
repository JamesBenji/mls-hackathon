"""Staged ElevenLabs photo tools. Never accept the shared static tool token here."""
import hashlib
import hmac
from datetime import timedelta
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import ForeignKey, String, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import models, timeutil
from app.database import Base, get_db
from app.followups import FollowUpPlan, PlanCall
from app.photos import push
from app.photos.models import PhotoRequest
from app.photos.quality import quality_out


class ToolPhotoRequest(Base):
    __tablename__ = "photo_tool_requests"
    request_id: Mapped[str] = mapped_column(ForeignKey("photo_requests.id"), primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("call_sessions.id"), unique=True)
    conversation_id: Mapped[str] = mapped_column(String(128))


class SessionIn(BaseModel):
    session_id: str = Field(min_length=1, max_length=32)
    conversation_id: str = Field(min_length=1, max_length=128)


class RequestPhotoIn(SessionIn):
    instructions: str = Field(min_length=1, max_length=2000)


class StatusIn(SessionIn):
    request_id: str = Field(min_length=1, max_length=36)


router = APIRouter(prefix="/tools", tags=["photo-tools"])
Db = Annotated[Session, Depends(get_db)]


def authorize(db, body, authorization):
    try:
        session_id = int(body.session_id)
    except ValueError:
        raise HTTPException(401, "Invalid session credential") from None
    linked = db.get(PlanCall, session_id)
    token = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
    digest = hashlib.sha256(token.encode()).hexdigest()
    if not token or not linked or not linked.credential_hash or not hmac.compare_digest(
            digest, linked.credential_hash):
        raise HTTPException(401, "Invalid session credential")
    session = db.get(models.CallSession, session_id)
    plan = db.get(FollowUpPlan, linked.plan_id)
    patient = db.get(models.Patient, session.patient_id)
    if (session.conversation_id != body.conversation_id or session.status != "in_progress"
            or session.patient_id != plan.patient_id or patient.doctor_id != plan.doctor_id):
        raise HTTPException(403, "Session/conversation association is not valid")
    if plan.status != "active" or not linked.photo_allowed or plan.photo_policy == "none":
        raise HTTPException(403, "Doctor has not approved photos for this call")
    if session.started_at and timeutil.utcnow() - session.started_at > timedelta(hours=4):
        raise HTTPException(401, "Session credential expired")
    return linked, plan


def output(db, request):
    return {"request_id": request.id, "push_status": request.push_status,
            **quality_out(db, request.id)}


@router.post("/request_photo")
def request_photo(body: RequestPhotoIn, db: Db,
                  authorization: str | None = Header(default=None),
                  idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128)):
    linked, plan = authorize(db, body, authorization)
    if not hmac.compare_digest(idempotency_key, linked.photo_request_key):
        raise HTTPException(403, "Invalid photo request key")
    binding = db.scalar(select(ToolPhotoRequest).where(ToolPhotoRequest.session_id == linked.session_id))
    if binding:
        request = db.get(PhotoRequest, binding.request_id)
        if request.instructions != body.instructions:
            raise HTTPException(409, "Idempotency key already used with different instructions")
        return output(db, request)
    request = PhotoRequest(id=str(uuid4()), patient_id=plan.patient_id,
                           doctor_id=plan.doctor_id, instructions=body.instructions,
                           idempotency_key=linked.photo_request_key)
    db.add(request)
    try:
        db.flush()
        db.add(ToolPhotoRequest(request_id=request.id, session_id=linked.session_id,
                               conversation_id=body.conversation_id))
        db.commit()  # Durable request and session binding before FCM.
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Concurrent request; retry with the same key") from None
    push.deliver(db, request)
    return output(db, request)


@router.post("/check_photo_status")
def status(body: StatusIn, db: Db,
           authorization: str | None = Header(default=None)):
    linked, _ = authorize(db, body, authorization)
    binding = db.get(ToolPhotoRequest, body.request_id)
    if not binding or binding.session_id != linked.session_id:
        raise HTTPException(404, "Photo request not found for this session")
    return output(db, db.get(PhotoRequest, body.request_id))
