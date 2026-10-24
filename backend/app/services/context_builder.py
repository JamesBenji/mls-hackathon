"""Builds channel-neutral context + ElevenLabs dynamic variables for a patient.

CONTRACT (implemented by the context+scheduler task):

    DEFAULT_DOCTOR_QUESTIONS: list[str]     SCOPE.md question bank defaults
    UNKNOWN_CALLER_PLAN = "Ask how you can help and note their name and issue."

    load_patient_context(db, patient_id) -> PatientContext
        Raises LookupError if the patient does not exist. vitals_trend = last
        7 days, oldest first.
    load_last_reports(db, patient_id, n: int | None = None) -> list[ReportSummary]
        Newest first, n defaults to settings.history_reports_n. Only reports
        with status != "failed".
    doctor_questions_for(db, patient_id, call_job_id: int | None = None) -> list[str]
        call_job.questions first (if any), then the patient's own template,
        else the doctor's default template (patient_id NULL), else
        DEFAULT_DOCTOR_QUESTIONS. De-duplicated, order preserved.
    last_call_summary(reports: list[ReportSummary]) -> str
        Most recent summary (with local date), or "none".
    async build_dynamic_variables(db, session: models.CallSession, *,
                                  resume_from: str = "start")
        -> tuple[dict[str, str], QuestionPlan]
        All five REQUIRED_DYNAMIC_VARIABLES, every value a str. Uses
        clinical.build_question_plan (which already falls back within
        PLAN_TIMEOUT_S) and clinical.render_plan_for_agent.
    unknown_caller_variables(session_id: int) -> dict[str, str]
        patient_name="caller", question_plan=UNKNOWN_CALLER_PLAN,
        last_call_summary="none", session_id=str(id), resume_from="start".
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app import models
from app.schemas.clinical import QuestionPlan
from app.schemas.context import PatientContext, ReportSummary

DEFAULT_DOCTOR_QUESTIONS: list[str] = [
    "Did you take your blood pressure today? What was the reading?",
    "Did you check your blood sugar? What was the reading?",
    "Did you take your medication today, and at what time?",
    "How many hours did you sleep last night?",
    "How are you feeling overall today?",
]

UNKNOWN_CALLER_PLAN = "Ask how you can help and note their name and issue."


def load_patient_context(db: Session, patient_id: int) -> PatientContext:
    raise NotImplementedError


def load_last_reports(db: Session, patient_id: int, n: int | None = None) -> list[ReportSummary]:
    raise NotImplementedError


def doctor_questions_for(db: Session, patient_id: int, call_job_id: int | None = None) -> list[str]:
    raise NotImplementedError


def last_call_summary(reports: list[ReportSummary]) -> str:
    raise NotImplementedError


async def build_dynamic_variables(db: Session, session: models.CallSession, *,
                                  resume_from: str = "start") -> tuple[dict[str, str], QuestionPlan]:
    raise NotImplementedError


def unknown_caller_variables(session_id: int) -> dict[str, str]:
    raise NotImplementedError
