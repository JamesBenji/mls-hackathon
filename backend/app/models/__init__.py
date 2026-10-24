"""SQLAlchemy models. Mirrors the data model in plan.md, plus the columns
the telephony + clinical tasks need.

Channel-agnostic core: `call_sessions` carry direction + channel so the
same tables serve phone, SMS, and future web/app channels.

All datetimes are naive UTC (see app/timeutil.py).
"""
from __future__ import annotations

from datetime import date, datetime

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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, JSONType, StrList


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
    phone: Mapped[str] = mapped_column(String(32), index=True)  # E.164
    dob: Mapped[date | None] = mapped_column(Date, nullable=True)
    blood_type: Mapped[str | None] = mapped_column(String(8), nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    chronic_conditions: Mapped[list[str]] = mapped_column(StrList, default=list)
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
    # bp_systolic|bp_diastolic|blood_sugar|weight|sleep_hours
    type: Mapped[str] = mapped_column(String(32))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    source: Mapped[str] = mapped_column(String(16), default="manual")  # call|manual|sms
    call_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("call_sessions.id"), nullable=True
    )

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
    patient_id: Mapped[int | None] = mapped_column(
        ForeignKey("patients.id"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(16))  # visit|call|manual
    # Rendered "Agent: ...\nPatient: ..." text (what the models read).
    text: Mapped[str] = mapped_column(Text)
    call_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("call_sessions.id"), nullable=True, index=True
    )
    # ElevenLabs conversation id; UNIQUE so duplicate webhooks dedupe.
    conversation_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True
    )
    turns: Mapped[list | None] = mapped_column(JSONType, nullable=True)  # raw turns
    analysis: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CallSession(Base):
    __tablename__ = "call_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    # Nullable: an inbound call from an unknown number still gets a session.
    patient_id: Mapped[int | None] = mapped_column(
        ForeignKey("patients.id"), nullable=True
    )
    direction: Mapped[str] = mapped_column(String(16))  # outbound|inbound
    channel: Mapped[str] = mapped_column(String(16), default="voice")  # voice|sms
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="scheduled")
    # scheduled|in_progress|callback_pending|completed|failed|missed|escalated
    callback_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # {"text": "...asked/answered so far...", "missing_item": "blood pressure",
    #  "conversation_id": "..."}  -> becomes `resume_from` on the callback.
    checkpoint: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # Latest attempt's ids (full history lives in call_attempts).
    conversation_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    call_sid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)  # calls placed so far
    caller_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    call_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("call_jobs.id"), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CallAttempt(Base):
    """One placed/received phone call within a session.

    A session can span several conversations (initial call + "call me back"
    resumes); the report is generated once over all of them.
    """
    __tablename__ = "call_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    call_session_id: Mapped[int] = mapped_column(
        ForeignKey("call_sessions.id"), index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    conversation_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True
    )
    call_sid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resume_from: Mapped[str] = mapped_column(Text, default="start")
    # placed|mock|completed|failed|missed
    status: Mapped[str] = mapped_column(String(16), default="placed")
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CallReport(Base):
    """Agent memory lives here — past reports feed the next call's context.

    Always a DRAFT for the doctor; the model never has the final word.
    """
    __tablename__ = "call_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    call_session_id: Mapped[int] = mapped_column(ForeignKey("call_sessions.id"))
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok|partial|failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    soap: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    soap_note: Mapped[str | None] = mapped_column(Text, nullable=True)  # rendered S/O/A/P
    structured_data: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # CapturedData
    risk_flags: Mapped[list[str]] = mapped_column(StrList, default=list)
    changes_since_last_call: Mapped[list[str]] = mapped_column(StrList, default=list)
    clinical_analysis: Mapped[str | None] = mapped_column(Text, nullable=True)  # stage-1 text
    # Suggested patients.risk_level — NOT auto-applied; doctor decides.
    proposed_risk_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    model_info: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    pdf_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TriageDecision(Base):
    __tablename__ = "triage_decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    call_session_id: Mapped[int] = mapped_column(ForeignKey("call_sessions.id"))
    patient_id: Mapped[int | None] = mapped_column(
        ForeignKey("patients.id"), nullable=True
    )
    urgency: Mapped[str] = mapped_column(String(16))  # none|monitor|urgent|emergency
    recommended_action: Mapped[str] = mapped_column(String(32))
    # none|doctor_callback|ambulance|request_appointment
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    # pipeline (rules+llm after the call) | agent_tool (flag_emergency
    # mid-call) | sms (inbound text) | system (e.g. unreachable patient)
    source: Mapped[str] = mapped_column(String(16), default="pipeline")
    rule_hits: Mapped[list[str]] = mapped_column(StrList, default=list)
    llm_urgency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    uncertain_because: Mapped[list[str]] = mapped_column(StrList, default=list)
    doctor_reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CallJob(Base):
    """Created by the doctor's personal agent (voice or typed)."""
    __tablename__ = "call_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"))
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    source: Mapped[str] = mapped_column(String(16), default="typed")  # voice|typed
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    situation: Mapped[str | None] = mapped_column(Text, nullable=True)
    treatment: Mapped[str | None] = mapped_column(Text, nullable=True)
    questions: Mapped[list[str]] = mapped_column(StrList, default=list)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|scheduled|done
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class QuestionTemplate(Base):
    __tablename__ = "question_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"))
    # NULL = the doctor's default template for all their patients.
    patient_id: Mapped[int | None] = mapped_column(ForeignKey("patients.id"), nullable=True)
    questions: Mapped[list[str]] = mapped_column(StrList, default=list)


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
    # Nullable: texts from unknown numbers are still logged.
    patient_id: Mapped[int | None] = mapped_column(
        ForeignKey("patients.id"), nullable=True
    )
    direction: Mapped[str] = mapped_column(String(16), default="outbound")  # outbound|inbound
    to_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    from_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    # reminder|tip|callback|missed|photo_link|reply|alert|inbound|other
    kind: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16), default="sent")  # sent|mock|failed|received
    provider_sid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
