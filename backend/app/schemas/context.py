"""Channel-neutral patient context handed from the DB layer to the clinical
service. The clinical service never touches the ORM; context_builder
produces these objects and the orchestrator persists the results.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MedicationInfo(BaseModel):
    name: str
    dose: str | None = None
    schedule: str | None = None
    is_new_prescription: bool = False


class VitalReading(BaseModel):
    type: str  # bp_systolic|bp_diastolic|blood_sugar|weight|sleep_hours
    value: float
    unit: str | None = None
    recorded_at: datetime | None = None  # naive UTC


class PatientContext(BaseModel):
    id: int | None = None  # None for an unknown inbound caller
    name: str
    phone: str | None = None
    category: str | None = None
    risk_level: str = "low"
    chronic_conditions: list[str] = Field(default_factory=list)
    medications: list[MedicationInfo] = Field(default_factory=list)
    vitals_trend: list[VitalReading] = Field(default_factory=list)  # last 7 days, oldest first

    @property
    def has_new_prescription(self) -> bool:
        return any(m.is_new_prescription for m in self.medications)


class ReportSummary(BaseModel):
    """One past call report = agent memory."""
    id: int | None = None
    created_at: datetime | None = None  # naive UTC
    summary: str | None = None
    risk_flags: list[str] = Field(default_factory=list)
    captured: dict | None = None  # CapturedData as dict (structured_data column)
