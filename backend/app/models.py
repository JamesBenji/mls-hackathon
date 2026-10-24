"""SQLAlchemy models — mirrors the data model in SCOPE.md.

Channel-agnostic core: `call_sessions` carry direction + channel so the
same tables serve phone, SMS, and future web/app channels.
"""
from __future__ import annotations

from datetime import datetime, date

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Doctor(Base):
    __tablename__ = "doctors"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(200), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)

    patients: Mapped[list[Patient]] = relationship(back_populates="doctor")


class Patient(Base):
    __tablename__ = "patients"
    id: Mapped[int] = mapped_column(primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"))
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(32))
    dob: Mapped[date | None] = mapped_column(Date, nullable=True)
    blood_type: Mapped[str | None] = mapped_column(String(8), nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    chronic_conditions: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    risk_level: Mapped[str] = mapped_column(String(16), default="low")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    doctor: Mapped[Doctor] = relationship(back_populates="patients")
    vitals: Mapped[list[Vital]] = relationship(back_populates="patient")
    medications: Mapped[list[Medication]] = relationship(back_populates="patient")


class Vital(Base):
    __tablename__ = "vitals"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    type: Mapped[str] = mapped_column(String(32))  # bp_systolic|blood_sugar|weight|...
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    source: Mapped[str] = mapped_column(String(16), default="manual")  # call|manual|sms

    patient: Mapped[Patient] = relationship(back_populates="vitals")


class Medication(Base):
    __tablename__ = "medications"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    name: Mapped[str] = mapped_column(String(120))
    dose: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schedule: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_new_prescription: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[date | None] = mapped_column(Date, nullable=True)

    patient: Mapped[Patient] = relationship(back_populates="medications")


class Visit(Base):
    __tablename__ = "visits"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    date: Mapped[date] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcription_id: Mapped[int | None] = mapped_column(
        ForeignKey("transcriptions.id"), nullable=True
    )


class Transcription(Base):
    __tablename__ = "transcriptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    source: Mapped[str] = mapped_column(String(16))  # visit|call
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CallSession(Base):
    __tablename__ = "call_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    direction: Mapped[str] = mapped_column(String(16))  # outbound|inbound
    channel: Mapped[str] = mapped_column(String(16), default="voice")  # voice|sms
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="scheduled")
    # scheduled|in_progress|callback_pending|completed|failed|missed
    callback_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    checkpoint: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class CallReport(Base):
    """Agent memory lives here — past reports feed the next call's context."""
    __tablename__ = "call_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    call_session_id: Mapped[int] = mapped_column(ForeignKey("call_sessions.id"))
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    soap_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    risk_flags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    pdf_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TriageDecision(Base):
    __tablename__ = "triage_decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    call_session_id: Mapped[int] = mapped_column(ForeignKey("call_sessions.id"))
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    urgency: Mapped[str] = mapped_column(String(16))  # none|monitor|urgent|emergency
    recommended_action: Mapped[str] = mapped_column(String(32))
    # none|doctor_callback|ambulance|request_appointment
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    doctor_reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CallJob(Base):
    """Created by the doctor's personal agent (voice or typed)."""
    __tablename__ = "call_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"))
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    source: Mapped[str] = mapped_column(String(16), default="typed")  # voice|typed
    situation: Mapped[str | None] = mapped_column(Text, nullable=True)
    treatment: Mapped[str | None] = mapped_column(Text, nullable=True)
    questions: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class QuestionTemplate(Base):
    __tablename__ = "question_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"))
    patient_id: Mapped[int | None] = mapped_column(ForeignKey("patients.id"), nullable=True)
    questions: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)


class Appointment(Base):
    __tablename__ = "appointments"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime)
    type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class PhotoUpload(Base):
    __tablename__ = "photo_uploads"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    call_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("call_sessions.id"), nullable=True
    )
    url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    upload_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="sms")  # sms|web_link
    assessment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SmsLog(Base):
    __tablename__ = "sms_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    body: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    kind: Mapped[str] = mapped_column(String(24))  # reminder|tip|callback|missed|photo_link
