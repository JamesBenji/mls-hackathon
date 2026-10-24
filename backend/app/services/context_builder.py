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

Implementation notes:
- Questions are de-duplicated case-, whitespace- and trailing-punctuation-
  insensitively; the first wording wins. A template whose question list is
  empty counts as missing, so the next fallback level applies.
- Questions from a call job that belongs to a different patient are ignored
  (logged), so one patient's situation never leaks into another's call.
- The live-call path never fails because of the clinical module: on top of
  build_question_plan's own PLAN_TIMEOUT_S fallback there is an outer
  wait_for (PLAN_TIMEOUT_S + PLAN_GRACE_S), and if clinical.fallback_plan /
  render_plan_for_agent themselves fail, a local static plan / numbered list
  is used.
- A session without a patient (unknown inbound caller) gets
  unknown_caller_variables() and an empty plan instead of a LookupError.
- build_dynamic_variables(..., plan_timeout_s=...) optionally shortens the
  model budget (0 = static doctor questions, no model call) for paths that
  must answer fast, like inbound voice-init.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models, timeutil
from app.adapters import elevenlabs
from app.config import settings
from app.schemas.clinical import PlannedQuestion, QuestionPlan
from app.schemas.context import (
    MedicationInfo,
    PatientContext,
    ReportSummary,
    VitalReading,
)
from app.services import clinical

log = logging.getLogger(__name__)

DEFAULT_DOCTOR_QUESTIONS: list[str] = [
    "Did you take your blood pressure today? What was the reading?",
    "Did you check your blood sugar? What was the reading?",
    "Did you take your medication today, and at what time?",
    "How many hours did you sleep last night?",
    "How are you feeling overall today?",
]

UNKNOWN_CALLER_PLAN = "Ask how you can help and note their name and issue."

VITALS_TREND_DAYS = 7
# Extra seconds on top of settings.plan_timeout_s before we stop waiting for
# clinical.build_question_plan ourselves (it should already have fallen back).
PLAN_GRACE_S = 2.0
# Keep the last-call summary short: the agent only recaps it briefly.
SUMMARY_MAX_CHARS = 600

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_TRAILING_PUNCT = re.compile(r"[\s.?!]+$")


# --- patient context -------------------------------------------------------

def load_patient_context(db: Session, patient_id: int) -> PatientContext:
    patient = db.get(models.Patient, patient_id) if patient_id is not None else None
    if patient is None:
        raise LookupError(f"patient {patient_id} not found")

    since = timeutil.utcnow() - timedelta(days=VITALS_TREND_DAYS)
    vitals = db.scalars(
        select(models.Vital)
        .where(models.Vital.patient_id == patient.id, models.Vital.recorded_at >= since)
        .order_by(models.Vital.recorded_at.asc(), models.Vital.id.asc())
    ).all()
    meds = db.scalars(
        select(models.Medication)
        .where(models.Medication.patient_id == patient.id)
        .order_by(models.Medication.id.asc())
    ).all()

    return PatientContext(
        id=patient.id,
        name=patient.name,
        phone=patient.phone,
        category=patient.category,
        risk_level=patient.risk_level or "low",
        chronic_conditions=list(patient.chronic_conditions or []),
        medications=[
            MedicationInfo(name=m.name, dose=m.dose, schedule=m.schedule,
                           is_new_prescription=bool(m.is_new_prescription))
            for m in meds
        ],
        vitals_trend=[
            VitalReading(type=v.type, value=v.value, unit=v.unit, recorded_at=v.recorded_at)
            for v in vitals
        ],
    )


def load_last_reports(db: Session, patient_id: int, n: int | None = None) -> list[ReportSummary]:
    limit = settings.history_reports_n if n is None else n
    if limit <= 0:
        return []
    rows = db.scalars(
        select(models.CallReport)
        .where(models.CallReport.patient_id == patient_id,
               models.CallReport.status != "failed")
        .order_by(models.CallReport.created_at.desc(), models.CallReport.id.desc())
        .limit(limit)
    ).all()
    return [
        ReportSummary(id=r.id, created_at=r.created_at, summary=r.summary,
                      risk_flags=list(r.risk_flags or []), captured=r.structured_data)
        for r in rows
    ]


# --- doctor questions ------------------------------------------------------

def _clean(text: str) -> str:
    return " ".join(text.split())


def _question_key(text: str) -> str:
    return _TRAILING_PUNCT.sub("", _clean(text)).casefold()


def _dedupe(questions: Iterable[object]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for q in questions:
        if not isinstance(q, str):
            continue
        key = _question_key(q)
        if key and key not in seen:
            seen.add(key)
            out.append(_clean(q))
    return out


def _newest_template(db: Session, *conditions) -> models.QuestionTemplate | None:
    return db.scalars(
        select(models.QuestionTemplate)
        .where(*conditions)
        .order_by(models.QuestionTemplate.id.desc())
        .limit(1)
    ).first()


def _template_questions(db: Session, patient_id: int) -> list[str]:
    """Patient's own template, else the doctor's default, else the bank."""
    own = _newest_template(db, models.QuestionTemplate.patient_id == patient_id)
    if own is not None and _dedupe(own.questions or []):
        return list(own.questions)
    patient = db.get(models.Patient, patient_id) if patient_id is not None else None
    if patient is not None:
        default = _newest_template(db, models.QuestionTemplate.doctor_id == patient.doctor_id,
                                   models.QuestionTemplate.patient_id.is_(None))
        if default is not None and _dedupe(default.questions or []):
            return list(default.questions)
    return list(DEFAULT_DOCTOR_QUESTIONS)


def doctor_questions_for(db: Session, patient_id: int, call_job_id: int | None = None) -> list[str]:
    questions: list[str] = []
    if call_job_id is not None:
        job = db.get(models.CallJob, call_job_id)
        if job is None:
            log.warning("call job %s not found; using template questions only", call_job_id)
        elif job.patient_id != patient_id:
            log.warning("call job %s belongs to patient %s, not %s; ignoring its questions",
                        call_job_id, job.patient_id, patient_id)
        else:
            questions.extend(job.questions or [])
    questions.extend(_template_questions(db, patient_id))
    return _dedupe(questions)


# --- last call summary -----------------------------------------------------

def _naive_utc(dt: datetime) -> datetime:
    # DB convention is naive UTC; tolerate aware values too.
    return dt if dt.tzinfo is None else dt.astimezone(UTC).replace(tzinfo=None)


def _spoken_date(dt: datetime) -> str:
    """'18 Oct' in the clinic timezone (year added if not the current one)."""
    local = timeutil.to_local(_naive_utc(dt))
    text = f"{local.day} {_MONTHS[local.month - 1]}"
    if local.year != timeutil.now_local().year:
        text += f" {local.year}"
    return text


def last_call_summary(reports: list[ReportSummary]) -> str:
    # Most recent report that actually has a summary (a partial report may
    # have none). The sort is stable, so undated reports keep caller order.
    ordered = sorted(
        reports, reverse=True,
        key=lambda r: _naive_utc(r.created_at) if r.created_at else datetime.min,
    )
    for report in ordered:
        text = _clean(report.summary or "")
        if not text:
            continue
        if len(text) > SUMMARY_MAX_CHARS:
            text = text[: SUMMARY_MAX_CHARS - 3].rstrip() + "..."
        if report.created_at is None:
            return text
        return f"On {_spoken_date(report.created_at)}: {text}"
    return "none"


# --- dynamic variables -----------------------------------------------------

def _static_plan(questions: list[str]) -> QuestionPlan:
    """clinical.fallback_plan, or an equivalent local plan if even that fails."""
    try:
        return clinical.fallback_plan(questions)
    except Exception:
        log.exception("clinical.fallback_plan failed; using a local static plan")
        return QuestionPlan(questions=[
            PlannedQuestion(id=f"q{i}", text=q, source="doctor")
            for i, q in enumerate(questions, 1)
        ])


async def _question_plan(patient: PatientContext, reports: list[ReportSummary],
                         questions: list[str], *, session_id: int | None,
                         budget_s: float | None = None) -> QuestionPlan:
    if budget_s is not None and budget_s <= 0:
        return _static_plan(questions)
    budget = settings.plan_timeout_s + PLAN_GRACE_S if budget_s is None else budget_s
    try:
        plan = await asyncio.wait_for(
            clinical.build_question_plan(patient, reports, questions), timeout=budget)
        if isinstance(plan, QuestionPlan) and plan.questions:
            return plan
        log.warning("empty question plan for session %s; using the doctor's questions",
                    session_id)
    except Exception as exc:  # timeout, model error, bug: the call must still go out
        log.warning("question plan failed for session %s (%s); using the doctor's questions",
                    session_id, type(exc).__name__)
    return _static_plan(questions)


def _render(plan: QuestionPlan) -> str:
    try:
        text = clinical.render_plan_for_agent(plan)
        if isinstance(text, str) and text.strip():
            return text
    except Exception:
        log.exception("clinical.render_plan_for_agent failed; using a plain numbered list")
    return "\n".join(f"{i}. {q.text}" for i, q in enumerate(plan.questions, 1))


async def build_dynamic_variables(db: Session, session: models.CallSession, *,
                                  resume_from: str = "start",
                                  plan_timeout_s: float | None = None,
                                  ) -> tuple[dict[str, str], QuestionPlan]:
    """See CONTRACT. plan_timeout_s overrides how long we wait for the model
    plan (default PLAN_TIMEOUT_S + PLAN_GRACE_S); 0 skips the model and uses
    the doctor's static questions, e.g. for the latency-critical inbound
    voice-init webhook."""
    resume = (resume_from or "").strip() or "start"
    if session.id is None:  # session_id must be our call_sessions.id
        db.add(session)
        db.flush()
    if session.patient_id is None:
        log.info("session %s has no patient; using unknown-caller variables", session.id)
        return unknown_caller_variables(session.id, resume_from=resume), QuestionPlan(questions=[])

    patient = load_patient_context(db, session.patient_id)  # LookupError propagates
    reports = load_last_reports(db, session.patient_id)
    from app.followups import PlanCall
    linked = db.get(PlanCall, session.id)
    if linked:
        import hashlib
        import secrets
        import json
        approved = linked.approved_context
        plan = _static_plan(approved["questions"])
        summary = db.scalar(select(models.CallReport.summary).join(
            PlanCall, PlanCall.session_id == models.CallReport.call_session_id
        ).where(PlanCall.plan_id == linked.plan_id).order_by(models.CallReport.id.desc()))
        token = secrets.token_urlsafe(32)
        linked.credential_hash = hashlib.sha256(token.encode()).hexdigest()
        values = {
            "patient_name": patient.name,
            "question_plan": "Doctor-approved follow-up only. Do not invent thresholds or treatment.\n"
                + json.dumps(approved, ensure_ascii=False)
                + "\nPhoto request allowed this session: " + str(linked.photo_allowed),
            "last_call_summary": (summary or "none")[:SUMMARY_MAX_CHARS],
            "session_id": str(session.id), "resume_from": resume,
            "secret__photo_tool_authorization": f"Bearer {token}",
            "photo_request_key": linked.photo_request_key,
        }
        db.commit()  # Credential must be durable before the call can invoke a tool.
        return values, plan
    questions = doctor_questions_for(db, session.patient_id, session.call_job_id)
    plan = await _question_plan(patient, reports, questions, session_id=session.id,
                                budget_s=plan_timeout_s)

    values = {
        "patient_name": (patient.name or "").strip() or "caller",
        "question_plan": _render(plan),
        "last_call_summary": last_call_summary(reports),
        "session_id": str(session.id),
        "resume_from": resume,
    }
    if session.call_job_id:
        job = db.get(models.CallJob, session.call_job_id)
        if job and job.patient_id == session.patient_id:
            values["question_plan"] = (
                f"Doctor-approved situation: {job.situation or ''}\n"
                f"Doctor-approved treatment context (do not alter): {job.treatment or ''}\n"
                + values["question_plan"])
    return {k: str(values[k]) for k in elevenlabs.REQUIRED_DYNAMIC_VARIABLES}, plan


def unknown_caller_variables(session_id: int, *, resume_from: str = "start") -> dict[str, str]:
    values = {
        "patient_name": "caller",
        "question_plan": UNKNOWN_CALLER_PLAN,
        "last_call_summary": "none",
        "session_id": str(session_id),
        "resume_from": (resume_from or "").strip() or "start",
    }
    return {k: values[k] for k in elevenlabs.REQUIRED_DYNAMIC_VARIABLES}
