"""Clinical service: the seam between the channel layer and the models
(MEDGEMMA_DO_SETUP.md §6). Pure functions over channel-neutral inputs
(app/schemas/context.py) -> validated Pydantic outputs
(app/schemas/clinical.py). No ORM / DB access in here; the orchestrator
persists results.

Everything produced here is a DRAFT for the doctor. Nothing in this module
is ever spoken to the patient.

CONTRACT (implemented by the clinical task):

    DEFAULT_REDFLAG_PROBE: str

    async build_question_plan(patient: PatientContext,
                              last_reports: list[ReportSummary],
                              doctor_questions: list[str]) -> QuestionPlan
        GEMMA4_MODEL chat_json, temperature 0, asyncio.wait_for(PLAN_TIMEOUT_S).
        Keeps ALL doctor questions, <= 3 history follow-ups, always a
        red-flag probe, side-effect probe iff a new prescription exists else
        improvement question, no diagnostic statements. Deterministic
        post-check enforces those rules on the model output.
        Timeout / ModelError / invalid output -> fallback_plan(doctor_questions)
        + log warning. NEVER raises.

    fallback_plan(doctor_questions: list[str]) -> QuestionPlan
        The doctor's static questions, in order (source="doctor").

    render_plan_for_agent(plan: QuestionPlan) -> str
        Numbered text for the ElevenLabs `question_plan` dynamic variable.

    async generate_report_detailed(transcript, patient, last_reports) -> GeneratedReport
    async generate_report(transcript, patient, last_reports) -> CallReportDraft
        two_stage: MEDGEMMA free-text analysis -> GEMMA4 chat_json draft.
        single: one GEMMA4 pass. Records only values the patient actually
        stated; unverifiable numbers are nulled and listed in missing_items.
        Raises ModelError if no draft can be produced (orchestrator marks the
        report partial and keeps the transcript).

    async triage_detailed(report: CallReportDraft | None, transcript, patient) -> TriageOutcome
    async triage(report, transcript, patient) -> TriageResult
        Stage 1 redflags.scan (min urgency "urgent"); stage 2 LLM chat_json;
        final urgency = max(rules, llm). Model failure -> rules-only result
        (at least "monitor", with uncertain_because). NEVER raises.

    propose_risk_level(patient, report, triage_result) -> str | None
        Suggested patients.risk_level (stored as a proposal, never applied).

    async extract_call_job(text: str, doctors_patients: list[dict], *,
                           now: datetime | None = None) -> CallJobExtraction
        doctors_patients = [{"id": int, "name": str}, ...] (that doctor's only).
        Fuzzy-match patient_ref; ambiguous/unknown -> clarification question.
        Relative times resolved against `now` (aware, Europe/Berlin) passed
        explicitly in the prompt, never the model's idea of the date.

    async compare_images(prior_b64: str, new_b64: str, context: str) -> str
        Phase 2 stub: MEDGEMMA with images_b64. No route/UI.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.schemas.clinical import (
    CallJobExtraction,
    CallReportDraft,
    QuestionPlan,
    TriageResult,
)
from app.schemas.context import PatientContext, ReportSummary

DEFAULT_REDFLAG_PROBE = (
    "Are you having any chest pain, trouble breathing, or severe dizziness right now?"
)


@dataclass
class GeneratedReport:
    draft: CallReportDraft
    clinical_analysis: str | None = None  # stage-1 MedGemma text (two_stage)
    model_info: dict = field(default_factory=dict)  # {"pipeline":..., "models": [...], "notes": [...]}


@dataclass
class TriageOutcome:
    result: TriageResult
    rule_hits: list[str] = field(default_factory=list)  # "category: matched"
    llm_urgency: str | None = None  # None if the model was unavailable


async def build_question_plan(patient: PatientContext, last_reports: list[ReportSummary],
                              doctor_questions: list[str]) -> QuestionPlan:
    raise NotImplementedError


def fallback_plan(doctor_questions: list[str]) -> QuestionPlan:
    raise NotImplementedError


def render_plan_for_agent(plan: QuestionPlan) -> str:
    raise NotImplementedError


async def generate_report_detailed(transcript: str, patient: PatientContext,
                                   last_reports: list[ReportSummary]) -> GeneratedReport:
    raise NotImplementedError


async def generate_report(transcript: str, patient: PatientContext,
                          last_reports: list[ReportSummary]) -> CallReportDraft:
    return (await generate_report_detailed(transcript, patient, last_reports)).draft


async def triage_detailed(report: CallReportDraft | None, transcript: str,
                          patient: PatientContext) -> TriageOutcome:
    raise NotImplementedError


async def triage(report: CallReportDraft | None, transcript: str,
                 patient: PatientContext) -> TriageResult:
    return (await triage_detailed(report, transcript, patient)).result


def propose_risk_level(patient: PatientContext, report: CallReportDraft | None,
                       triage_result: TriageResult) -> str | None:
    raise NotImplementedError


async def extract_call_job(text: str, doctors_patients: list[dict], *,
                           now: datetime | None = None) -> CallJobExtraction:
    raise NotImplementedError


async def compare_images(prior_b64: str, new_b64: str, context: str) -> str:
    raise NotImplementedError
