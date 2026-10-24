"""Validated model I/O for the clinical service (MEDGEMMA_DO_SETUP.md §6.1).

These classes double as the JSON Schemas passed to Ollama's `format` field
(`Model.model_json_schema()`), so keep them flat and simple.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Urgency = Literal["none", "monitor", "urgent", "emergency"]
Action = Literal["none", "doctor_callback", "ambulance", "request_appointment"]

URGENCY_ORDER: dict[str, int] = {"none": 0, "monitor": 1, "urgent": 2, "emergency": 3}


def max_urgency(*levels: str) -> str:
    """Highest of the given urgency levels (unknown values count as 'none')."""
    return max(levels, key=lambda u: URGENCY_ORDER.get(u, 0)) if levels else "none"


class PlannedQuestion(BaseModel):
    id: str
    text: str
    source: Literal["doctor", "history_followup", "redflag_probe"]
    skip_if_answered: bool = True


class QuestionPlan(BaseModel):
    questions: list[PlannedQuestion]


class Soap(BaseModel):
    subjective: str
    objective: str
    assessment: str
    plan: str


class CapturedData(BaseModel):
    bp_systolic: int | None = None
    bp_diastolic: int | None = None
    blood_sugar: float | None = None  # mg/dL
    weight_kg: float | None = None
    sleep_hours: float | None = None
    meds_taken: bool | None = None
    med_time: str | None = None
    side_effects: list[str] = Field(default_factory=list)
    symptoms: list[str] = Field(default_factory=list)
    # e.g. ["blood_pressure"] -> callback candidates.
    # Canonical items: blood_pressure | blood_sugar | weight | sleep | medication
    missing_items: list[str] = Field(default_factory=list)


class CallReportDraft(BaseModel):
    summary: str  # <= 3 sentences, doctor-facing
    soap: Soap
    captured: CapturedData
    risk_flags: list[str]
    changes_since_last_call: list[str]


class TriageResult(BaseModel):
    urgency: Urgency
    recommended_action: Action
    rationale: str
    uncertain_because: list[str] = Field(default_factory=list)  # what the model lacked


class CallJobDraft(BaseModel):  # doctor's personal agent
    patient_ref: str  # name or id as spoken/typed
    situation: str
    treatment: str
    questions: list[str]
    scheduled_at: str | None  # ISO 8601 in Europe/Berlin, or null if unspecified


class CallJobExtraction(BaseModel):
    """Result of extract_call_job(): either a resolved draft or a question.

    Exactly one of (`draft` + `patient_id`) or `clarification` is meaningful:
    if the patient reference is ambiguous/unknown we ask instead of guessing.
    """
    draft: CallJobDraft | None = None
    patient_id: int | None = None
    scheduled_at_utc: str | None = None  # parsed+validated, naive UTC ISO
    clarification: str | None = None
    candidates: list[dict] = Field(default_factory=list)  # [{id, name, score}]
