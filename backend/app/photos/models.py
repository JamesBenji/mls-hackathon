"""Additive tables; existing clinical tables are not altered."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app import models as clinical_models  # noqa: F401 -- register FK targets
from app.database import Base


def utcnow() -> datetime:
    # Match the clinical schema's naive UTC DateTime columns.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AccessCredential(Base):
    __tablename__ = "photo_access_credentials"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    role: Mapped[str] = mapped_column(String(16))
    patient_id: Mapped[int | None] = mapped_column(ForeignKey("patients.id"))
    doctor_id: Mapped[int | None] = mapped_column(ForeignKey("doctors.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class PatientDevice(Base):
    __tablename__ = "photo_patient_devices"
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    fcm_token: Mapped[str | None] = mapped_column(Text, unique=True)
    platform: Mapped[str] = mapped_column(String(16))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PhotoRequest(Base):
    __tablename__ = "photo_requests"
    __table_args__ = (UniqueConstraint("doctor_id", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    instructions: Mapped[str] = mapped_column(String(2000))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    push_status: Mapped[str] = mapped_column(String(24), default="not_sent")
    push_attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class PhotoAsset(Base):
    """Private metadata linked to the colleague's existing PhotoUpload record."""

    __tablename__ = "photo_assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("photo_requests.id"), unique=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    upload_id: Mapped[int] = mapped_column(ForeignKey("photo_uploads.id"), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    content_hash: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(64), unique=True)
    mime_type: Mapped[str] = mapped_column(String(32))
    size_bytes: Mapped[int] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


PHOTO_TABLES = [
    AccessCredential.__table__,
    PatientDevice.__table__,
    PhotoRequest.__table__,
    PhotoAsset.__table__,
]
