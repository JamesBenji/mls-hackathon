"""Clinical service: the seam between the channel layer and the model
(Gemma 4 via the cloud API). Pure functions over channel-neutral inputs
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
        GEMMA_MODEL chat_json, temperature 0, asyncio.wait_for(PLAN_TIMEOUT_S).
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
        two_stage: Gemma free-text analysis -> Gemma chat_json draft.
        single: one Gemma pass. Records only values the patient actually
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
        Phase 2 stub: Gemma vision with images_b64. No route/UI.

Extra public helpers (pure, deterministic, safe to reuse):
    render_patient_context(patient, last_reports, *, now=None) -> str
    postcheck_plan(plan, doctor_questions, patient) -> QuestionPlan
    finalize_report_draft(draft, transcript) -> (CallReportDraft, notes)
    resolve_patient_ref(ref, doctors_patients) -> PatientResolution
    parse_scheduled_at(value) -> str | None

Implementation notes:
- Every model call goes through app.adapters.gemma_client by module
  attribute (tests monkeypatch chat/chat_json) and reads model names and
  timeouts from settings at call time.
- The model never has the final word: the plan post-check, the extraction
  guard (a number only survives if the PATIENT said it) and the red-flag
  floor in triage (the LLM can only raise urgency) are deterministic.
- Wherever the model reasons about time, the current clinic-local date and
  time (timeutil.now_local, Europe/Berlin) is stated in the prompt.
- Logs carry error types and counts, never transcripts or prompts.
"""
from __future__ import annotations

import asyncio
import difflib
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pydantic import ValidationError

from app import timeutil
from app.adapters import gemma_client
from app.config import settings
from app.schemas import clinical as schemas
from app.schemas.clinical import (
    CallJobDraft,
    CallJobExtraction,
    CallReportDraft,
    CapturedData,
    PlannedQuestion,
    QuestionPlan,
    TriageResult,
)
from app.schemas.context import PatientContext, ReportSummary, VitalReading
from app.services import redflags, transcripts

log = logging.getLogger(__name__)

DEFAULT_REDFLAG_PROBE = (
    "Are you having any chest pain, trouble breathing, or severe dizziness right now?"
)
SIDE_EFFECTS_PROBE = "Have you noticed any side effects since starting {medications}?"
IMPROVEMENT_PROBE = "Have you noticed any improvement since starting your medication?"
IMPROVEMENT_PROBE_NO_MEDS = "Have you noticed any improvement since our last call?"
MAX_HISTORY_FOLLOWUPS = 3

NO_CHART_NOTE = (
    "No access to the full medical chart and no physical examination: "
    "this assessment is based on a phone check-in only."
)
MODEL_UNAVAILABLE_NOTE = (
    "The triage model was unavailable: this result comes from the "
    "deterministic red-flag rules only."
)
SCAN_FAILED_NOTE = "The deterministic red-flag scan failed: review the transcript manually."

# Canonical names for CapturedData.missing_items (callback candidates).
CANONICAL_MISSING_ITEMS = ("blood_pressure", "blood_sugar", "weight", "sleep", "medication")

# --- prompts ---------------------------------------------------------------

PLAN_SYSTEM_PROMPT = """\
You plan a short check-in phone call that Guardian, an AI assistant, makes to a \
patient on behalf of the patient's doctor. You only write the questions; the \
assistant asks them on the phone.

Rules:
1. Keep EVERY doctor-required question exactly as written, in the given order, \
with source "doctor" and its given id (d1, d2, ...).
2. Add at most 3 follow-ups based on the patient's history (previous calls, \
vitals trend), source "history_followup", ids h1, h2, h3. Examples: "Last week \
you mentioned a cough. Is it better or worse?" or "Your blood pressure was high \
on our last call. Has it come down?" Only mention things that are in the history.
3. If the patient has a NEW prescription, one follow-up asks about side effects \
of that medication. Otherwise one follow-up asks whether they have noticed any \
improvement.
4. Always include exactly one red-flag check, source "redflag_probe", id r1, \
asking about chest pain, trouble breathing, or severe dizziness.
5. Keep every question short, plain, and easy to understand when spoken on the \
phone. One question per item.
6. Never include a diagnosis, an interpretation of a value, medical advice, or \
a medication change. Only ask questions.
7. skip_if_answered is true unless the question must always be asked.

Return JSON matching the schema."""

REPORT_ANALYSIS_SYSTEM_PROMPT = """\
You are a clinical assistant helping a doctor review a follow-up phone call \
between Guardian (an AI assistant) and a patient after hospital discharge. \
Write a short clinical analysis for the DOCTOR, never for the patient, in plain \
text with these headings:
Reported today: symptoms, vital signs (quote the exact numbers the PATIENT \
said), medication adherence, side effects, sleep.
Changes since previous calls: compare with the history and the vitals trend.
Concerns: possible red flags or worsening, and why.
Medication issues: missed doses, timing, side effects, new prescriptions.
Missing information: anything asked but not provided (for example a blood \
pressure the patient has not measured yet).

Rules: use only the transcript and the history. Never invent or estimate \
values. Numbers spoken by the Agent (for example from the history) are not new \
measurements. Write "not reported" when something was not said. No definitive \
diagnosis. Be concise."""

REPORT_DRAFT_SYSTEM_PROMPT = """\
You turn a check-in phone call into a structured DRAFT report for the \
patient's doctor. The doctor reviews every draft.

summary: at most 3 short sentences for the doctor: the main findings and \
anything that needs attention.
soap: subjective = what the patient reported; objective = only values the \
patient stated in this call; assessment = cautious and clearly provisional, no \
definitive diagnosis; plan = follow-up for the doctor to consider, no \
medication changes.
captured: ONLY values the PATIENT explicitly stated in THIS call.
- Never copy values from the history, from the Agent's lines, or from the \
clinical analysis unless the patient said them. Never estimate, average or \
calculate.
- If the patient did not state a value, use null and add the item to \
missing_items.
- bp_systolic / bp_diastolic: integers in mmHg ("140 over 90" -> 140 and 90).
- blood_sugar: mg/dL (if the patient gave mmol/L, multiply by 18).
- weight_kg: kilograms (pounds x 0.4536). sleep_hours: hours slept last night.
- meds_taken: true or false, null if not discussed. med_time: as the patient \
said it, for example "08:00".
- side_effects, symptoms: short phrases in the patient's words, [] if none.
- missing_items: only these names: blood_pressure, blood_sugar, weight, sleep, \
medication.
risk_flags: short snake_case labels such as high_bp, low_blood_sugar, \
missed_medication, new_side_effect, chest_pain, worsening_symptom; [] if none.
changes_since_last_call: short phrases comparing with the previous calls; [] \
if this is the first call.

Return JSON matching the schema."""

TRIAGE_SYSTEM_PROMPT = """\
You are a triage assistant for a doctor who follows up patients after hospital \
discharge. Classify the urgency of one check-in call. Your result is a DRAFT \
that the doctor reviews; nothing you write is said to the patient.

urgency:
- none: routine, nothing new.
- monitor: something to watch or to raise at the next routine contact.
- urgent: the doctor should call the patient back today.
- emergency: possible immediate danger to life (for example ongoing chest pain, \
severe breathlessness, stroke signs, fainting, heavy bleeding, suicidal intent).
recommended_action: none | doctor_callback | ambulance | request_appointment.
Deterministic red-flag rules already ran and their hits are listed. Any hit \
means the urgency is at least "urgent"; you cannot lower that.
rationale: 1-3 sentences for the doctor, citing what the patient said. No \
diagnosis, no medication changes.
uncertain_because: what you could not know or check (for example vitals not \
measured today, no physical examination, no full chart).

Return JSON matching the schema."""

CALL_JOB_SYSTEM_PROMPT = """\
You turn a doctor's spoken or typed instruction into a call job for Guardian, \
an AI assistant that phones the doctor's patients.

- patient_ref: the patient's name or numeric id exactly as the doctor said it. \
Do not correct, complete or guess it.
- situation: the patient's current situation in one or two sentences.
- treatment: the current medication or treatment the doctor mentioned, or "".
- questions: the specific questions the doctor wants asked, as short spoken \
questions; [] if none.
- scheduled_at: when to call, as ISO 8601 clinic-local time without a UTC \
offset (YYYY-MM-DDTHH:MM:SS), resolved from the CURRENT date and time given \
below ("tomorrow at 9" means the next calendar day at 09:00). null if the \
doctor gave no time. Never invent a time.
Do not add medical advice.

Return JSON matching the schema."""

IMAGE_COMPARE_SYSTEM_PROMPT = """\
You assist a doctor with recovery tracking. You receive two images of the same \
patient (for example an X-ray or a wound photo): the FIRST is the earlier \
upload, the SECOND is the new one. Describe the visible changes between them \
(for example healing, alignment, swelling, redness, discharge), say how \
confident you are, and list what cannot be judged from images alone. This is a \
draft for the doctor only: no definitive diagnosis and nothing addressed to \
the patient."""


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


@dataclass
class PatientResolution:
    """Outcome of matching a spoken/typed patient reference."""
    patient_id: int | None = None
    clarification: str | None = None
    candidates: list[dict] = field(default_factory=list)  # [{id, name, score}]


# --- shared helpers ------------------------------------------------------------

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _unique(items) -> list:
    seen, out = set(), []
    for item in items:
        key = item.lower() if isinstance(item, str) else item
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _join_names(names: list[str], word: str = "and") -> str:
    names = [n for n in names if n]
    if len(names) <= 1:
        return names[0] if names else ""
    return f"{', '.join(names[:-1])} {word} {names[-1]}"


def _local_now(now: datetime | None = None) -> datetime:
    """Aware clinic-local time; a naive `now` is taken as local time."""
    if now is None:
        return timeutil.now_local()
    if now.tzinfo is None:
        return now.replace(tzinfo=timeutil.local_tz())
    return now.astimezone(timeutil.local_tz())


def _to_local(dt: datetime) -> datetime:
    return timeutil.to_local(dt) if dt.tzinfo is None else dt.astimezone(timeutil.local_tz())


def describe_now(now: datetime) -> str:
    """'Saturday, 2026-10-24 14:30 (Europe/Berlin)' (locale independent)."""
    return f"{_WEEKDAYS[now.weekday()]}, {now:%Y-%m-%d %H:%M} ({settings.timezone})"


def _messages(system: str, user: str) -> list[dict]:
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _as_model_error(exc: BaseException, what: str) -> Exception:
    if isinstance(exc, gemma_client.ModelError):
        return exc
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return gemma_client.ModelUnavailable(f"{what}: timed out")
    if isinstance(exc, ValidationError):
        return gemma_client.ModelOutputInvalid(f"{what}: output failed validation")
    return gemma_client.ModelError(f"{what}: {type(exc).__name__}: {exc}")


async def _chat_json(schema_model, messages: list[dict], what: str, timeout: float):
    """Gemma structured call; every failure surfaces as a ModelError."""
    try:
        out = await gemma_client.chat_json(settings.gemma_model, messages, schema_model,
                                           temperature=0, timeout=timeout)
        return out if isinstance(out, schema_model) else schema_model.model_validate(out)
    except Exception as exc:
        err = _as_model_error(exc, what)
        if err is exc:
            raise
        raise err from exc


# --- patient context for prompts ------------------------------------------------

_VITAL_LABELS = {"blood_sugar": "blood sugar", "weight": "weight", "weight_kg": "weight",
                 "sleep_hours": "sleep"}
_VITAL_UNITS = {"bp_systolic": "mmHg", "bp_diastolic": "mmHg", "blood_sugar": "mg/dL",
                "weight": "kg", "weight_kg": "kg", "sleep_hours": "h"}


def _stamp(dt: datetime | None) -> str:
    return f"{_to_local(dt):%Y-%m-%d %H:%M}" if dt is not None else "time unknown"


def _day(dt: datetime | None, now: datetime) -> str:
    if dt is None:
        return "date unknown"
    local = _to_local(dt)
    days = (now.date() - local.date()).days
    ago = ("today" if days == 0 else "yesterday" if days == 1
           else f"{days} days ago" if days > 1 else "in the future")
    return f"{local:%Y-%m-%d} ({ago})"


def _render_vitals(vitals: list[VitalReading]) -> list[str]:
    groups: dict[datetime | None, dict[str, VitalReading]] = {}
    for v in vitals:
        groups.setdefault(v.recorded_at, {}).setdefault(v.type, v)
    lines = []
    for when, readings in groups.items():
        parts = []
        sys_, dia = readings.pop("bp_systolic", None), readings.pop("bp_diastolic", None)
        if sys_ and dia:
            parts.append(f"BP {_fmt(sys_.value)}/{_fmt(dia.value)} {sys_.unit or 'mmHg'}")
        else:
            parts += [f"{r.type.replace('_', ' ')} {_fmt(r.value)} {r.unit or 'mmHg'}"
                      for r in (sys_, dia) if r]
        for vtype, r in readings.items():
            unit = r.unit or _VITAL_UNITS.get(vtype, "")
            label = _VITAL_LABELS.get(vtype, vtype.replace("_", " "))
            parts.append(f"{label} {_fmt(r.value)}{' ' + unit if unit else ''}")
        lines.append(f"- {_stamp(when)}: {', '.join(parts)}")
    return lines


def _captured_brief(captured: dict | None) -> str:
    if not captured:
        return ""
    parts = []
    sys_, dia = captured.get("bp_systolic"), captured.get("bp_diastolic")
    if sys_ is not None or dia is not None:
        parts.append(f"BP {sys_ if sys_ is not None else '?'}/{dia if dia is not None else '?'}")
    for key, label, unit in (("blood_sugar", "blood sugar", " mg/dL"), ("weight_kg", "weight", " kg"),
                             ("sleep_hours", "sleep", " h")):
        if captured.get(key) is not None:
            parts.append(f"{label} {captured[key]}{unit}")
    for key in ("symptoms", "side_effects", "missing_items"):
        if captured.get(key):
            parts.append(f"{key.replace('_', ' ')}: {', '.join(map(str, captured[key]))}")
    return "; ".join(parts)


def _render_reports(reports: list[ReportSummary], now: datetime) -> list[str]:
    lines = []
    for r in reports[: max(0, settings.history_reports_n)]:
        summary = " ".join((r.summary or "no summary").split())
        if len(summary) > 400:
            summary = summary[:397] + "..."
        line = f"- {_day(r.created_at, now)}: {summary}"
        if r.risk_flags:
            line += f" [risk flags: {', '.join(r.risk_flags)}]"
        brief = _captured_brief(r.captured)
        if brief:
            line += f" [recorded: {brief}]"
        lines.append(line)
    return lines


def render_patient_context(patient: PatientContext, last_reports: list[ReportSummary] | None = None,
                           *, now: datetime | None = None) -> str:
    """Patient context block shared by all prompts (dates in local time)."""
    now = _local_now(now)
    lines = [
        f"Name: {patient.name}",
        f"Category: {patient.category or 'not set'}",
        f"Risk level: {patient.risk_level or 'not set'}",
        f"Chronic conditions: {', '.join(patient.chronic_conditions) or 'none recorded'}",
    ]
    if patient.medications:
        lines.append("Medications:")
        for m in patient.medications:
            detail = ", ".join(x for x in (m.dose, m.schedule) if x)
            lines.append(f"- {m.name}{' ' + detail if detail else ''}"
                         f"{' [NEW PRESCRIPTION]' if m.is_new_prescription else ''}")
    else:
        lines.append("Medications: none recorded")
    vitals = _render_vitals(patient.vitals_trend)
    lines.append("Vitals, last 7 days (oldest first, local time):" if vitals
                 else "Vitals, last 7 days: none recorded")
    lines += vitals
    reports = _render_reports(last_reports or [], now)
    lines.append("Previous call reports (newest first):" if reports
                 else "Previous call reports: none (first call)")
    lines += reports
    return "\n".join(lines)


# --- question plan -------------------------------------------------------------

_SIDE_EFFECTS_RE = re.compile(r"side[\s-]?effects?|nebenwirkung\w*|reaction|unvertr(?:ä|ae)glich",
                              re.IGNORECASE)
_IMPROVEMENT_RE = re.compile(r"\bimprov\w*|\bbetter\b|\bbesser\w*|\bverbesser\w*", re.IGNORECASE)
_MEDICATION_RE = re.compile(
    r"\bmedicat\w*|\bmeds\b|\bmedicine\b|\bpills?\b|\btablets?\b|\bmedikament\w*|\btablette\w*",
    re.IGNORECASE)
# An unlabelled question about chest pain AND breathing/dizziness is the probe.
_PROBE_CHEST_RE = re.compile(r"chest pain|brustschmerz|schmerzen in der brust", re.IGNORECASE)
_PROBE_OTHER_RE = re.compile(r"breath|dizz|atemnot|luft|schwindel", re.IGNORECASE)
_DIAGNOSTIC_RE = re.compile(
    r"\bdiagnos\w*|\b(?:this|that|which|it)\s+(?:means|indicates|suggests|shows)\b"
    r"|\bconsistent\s+with\b|\bsounds\s+like\s+(?:a|an|you)\b"
    r"|\byou\s+(?:probably|likely|might|may)\s+have\b|\byou\s+should\b"
    r"|\byou\s+need\s+to\s+(?:stop|start|take|increase|reduce|change)\b"
    r"|\b(?:stop|increase|decrease|reduce|double|skip|change)\s+(?:taking\s+)?your\s+"
    r"(?:medication|meds|medicine|dose|dosage|pills?|tablets?)\b"
    r"|\bdeutet\s+auf\b|\bsie\s+sollten\b|\bsie\s+haben\s+(?:wahrscheinlich|vermutlich)\b",
    re.IGNORECASE)


def _norm_q(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").lower()
    return " ".join(re.sub(r"[^\w\s]", " ", t).split())


def _match_doctor(norm: str, doctor_norms: list[str], *, claimed_doctor: bool) -> int | None:
    if norm in doctor_norms:
        return doctor_norms.index(norm)
    if not doctor_norms:
        return None
    scores = [difflib.SequenceMatcher(None, norm, d).ratio() for d in doctor_norms]
    best = max(range(len(scores)), key=scores.__getitem__)
    # A question the model labels "doctor" is most likely a paraphrase; anything
    # else must be near-identical to count as the doctor's question.
    return best if scores[best] >= (0.6 if claimed_doctor else 0.9) else None


def _med_probe_text(patient: PatientContext) -> str:
    if patient.has_new_prescription:
        names = _join_names([m.name for m in patient.medications if m.is_new_prescription])
        return SIDE_EFFECTS_PROBE.format(medications=names or "your new medication")
    return IMPROVEMENT_PROBE if patient.medications else IMPROVEMENT_PROBE_NO_MEDS


def postcheck_plan(plan: QuestionPlan, doctor_questions: list[str],
                   patient: PatientContext) -> QuestionPlan:
    """Deterministically enforce the planning rules on a model plan.

    All doctor questions (verbatim, original order; dropped ones re-inserted
    where they belong), <= MAX_HISTORY_FOLLOWUPS follow-ups, exactly one
    red-flag probe, a side-effects (new prescription) or improvement question,
    no diagnostic statements, unique ids.
    """
    doctor = [q.strip() for q in doctor_questions if q and q.strip()]
    doctor_norms = [_norm_q(q) for q in doctor]

    # 1. Classify the model's questions.
    entries: list[tuple[str, int | PlannedQuestion]] = []
    doctor_skip: dict[int, bool] = {}
    seen_extra: set[str] = set()
    for q in plan.questions:
        text = " ".join((q.text or "").split())
        if not text:
            continue
        norm = _norm_q(text)
        idx = _match_doctor(norm, doctor_norms, claimed_doctor=q.source == "doctor")
        if idx is not None:
            doctor_skip.setdefault(idx, q.skip_if_answered)
            entries.append(("doctor", idx))
        elif _DIAGNOSTIC_RE.search(text):
            log.info("question plan: dropped a model question with diagnostic/advice wording")
        elif norm not in seen_extra:
            seen_extra.add(norm)
            source = q.source if q.source == "redflag_probe" else "history_followup"
            if (source == "history_followup" and _PROBE_CHEST_RE.search(norm)
                    and _PROBE_OTHER_RE.search(norm)):
                source = "redflag_probe"  # "any chest pain or trouble breathing?" unlabelled
            entries.append(("extra", PlannedQuestion(id="", text=text, source=source,
                                                     skip_if_answered=q.skip_if_answered)))

    # 2. Doctor questions in original order. A dropped one goes right before
    #    the next doctor question the model kept; trailing ones right after
    #    the last kept one (or first, if the model kept none).
    ordered: list[tuple[str, int | PlannedQuestion]] = []
    emitted: set[int] = set()
    insert_at = 0
    for kind, item in entries:
        if kind == "doctor":
            before = len(ordered)
            for i in range(item + 1):
                if i not in emitted:
                    emitted.add(i)
                    ordered.append(("doctor", i))
            if len(ordered) > before:
                insert_at = len(ordered)
        else:
            ordered.append((kind, item))
    ordered[insert_at:insert_at] = [("doctor", i) for i in range(len(doctor)) if i not in emitted]

    # 3. Exactly one red-flag probe.
    has_probe, kept = False, []
    for kind, item in ordered:
        if kind == "extra" and item.source == "redflag_probe":
            if has_probe:
                continue
            has_probe = True
        kept.append((kind, item))
    ordered = kept

    # 4. Medication question (side effects iff new prescription, else
    #    improvement) and the follow-up cap, which never drops that question.
    med_re = _SIDE_EFFECTS_RE if patient.has_new_prescription else _IMPROVEMENT_RE
    med_entry = next((e for e in ordered if e[0] == "extra" and e[1].source == "history_followup"
                      and med_re.search(e[1].text)), None)
    need_med = med_entry is None and not any(med_re.search(q) for q in doctor)
    budget = MAX_HISTORY_FOLLOWUPS - (1 if med_entry is not None or need_med else 0)
    kept, others = [], 0
    for entry in ordered:
        kind, item = entry
        if kind == "extra" and item.source == "history_followup" and entry is not med_entry:
            if others >= budget:
                continue
            others += 1
        kept.append(entry)
    ordered = kept
    if need_med:
        pos = len(ordered)
        for i, (kind, item) in enumerate(ordered):
            if kind == "doctor" and _MEDICATION_RE.search(doctor[item]):
                pos = i + 1
        ordered.insert(pos, ("extra", PlannedQuestion(id="", text=_med_probe_text(patient),
                                                      source="history_followup")))
    if not has_probe:
        ordered.append(("extra", PlannedQuestion(id="", text=DEFAULT_REDFLAG_PROBE,
                                                 source="redflag_probe")))

    # 5. Deterministic, unique ids. The red-flag probe is always asked.
    questions: list[PlannedQuestion] = []
    n_follow = n_probe = 0
    for kind, item in ordered:
        if kind == "doctor":
            questions.append(PlannedQuestion(id=f"d{item + 1}", text=doctor[item], source="doctor",
                                             skip_if_answered=doctor_skip.get(item, True)))
        elif item.source == "redflag_probe":
            n_probe += 1
            questions.append(item.model_copy(update={"id": f"r{n_probe}",
                                                     "skip_if_answered": False}))
        else:
            n_follow += 1
            questions.append(item.model_copy(update={"id": f"h{n_follow}"}))
    return QuestionPlan(questions=questions)


def _plan_messages(patient: PatientContext, last_reports: list[ReportSummary],
                   doctor_questions: list[str], now: datetime) -> list[dict]:
    doctor_block = "\n".join(f"d{i}. {q}" for i, q in enumerate(doctor_questions, 1)) or "(none)"
    if patient.has_new_prescription:
        names = _join_names([m.name for m in patient.medications if m.is_new_prescription])
        rx = f"NEW PRESCRIPTION: yes ({names}). Include one follow-up about side effects."
    else:
        rx = "NEW PRESCRIPTION: none. Include one follow-up asking about improvement."
    user = (f"Current date and time: {describe_now(now)}.\n\n"
            f"PATIENT CONTEXT\n{render_patient_context(patient, last_reports, now=now)}\n\n"
            f"DOCTOR-REQUIRED QUESTIONS (keep all, in this order)\n{doctor_block}\n\n{rx}")
    return _messages(PLAN_SYSTEM_PROMPT, user)


async def build_question_plan(patient: PatientContext, last_reports: list[ReportSummary],
                              doctor_questions: list[str]) -> QuestionPlan:
    questions = [q.strip() for q in (doctor_questions or []) if q and q.strip()]
    budget = settings.plan_timeout_s
    try:
        messages = _plan_messages(patient, last_reports or [], questions, _local_now())
        plan = await asyncio.wait_for(
            gemma_client.chat_json(settings.gemma_model, messages, QuestionPlan,
                                   temperature=0, timeout=budget),
            timeout=budget,
        )
        if not isinstance(plan, QuestionPlan):
            plan = QuestionPlan.model_validate(plan)
        return postcheck_plan(plan, questions, patient)
    except (TimeoutError, asyncio.TimeoutError):
        log.warning("question plan: model exceeded the %.1fs live-call budget; "
                    "using the doctor's static questions", budget)
    except Exception as exc:  # noqa: BLE001 - the call must never fail because of the model
        log.warning("question plan: %s; using the doctor's static questions", type(exc).__name__)
    return fallback_plan(questions)


def fallback_plan(doctor_questions: list[str]) -> QuestionPlan:
    questions = [q.strip() for q in (doctor_questions or []) if q and q.strip()]
    return QuestionPlan(questions=[
        PlannedQuestion(id=f"d{i}", text=q, source="doctor") for i, q in enumerate(questions, 1)
    ])


def render_plan_for_agent(plan: QuestionPlan) -> str:
    return "\n".join(f"{i}. {' '.join(q.text.split())}" for i, q in enumerate(plan.questions, 1))


# --- post-call report + extraction guard -------------------------------------------

_ROLE_LINE_RE = re.compile(r"^\s*(agent|patient|user|assistant|ai|guardian)\s*:\s*(.*)$",
                           re.IGNORECASE)  # same roles as app.services.transcripts
_PATIENT_ROLES = {"patient", "user"}

# Vital topics: which numbers the patient said while a vital was discussed.
_TOPIC_RES = {
    "bp": re.compile(
        r"\b(?:blood\s*pressure|bp|pressure|systolic|diastolic|(?:top|bottom|upper|lower)\s+number"
        r"|blutdruck\w*|systolisch\w*|diastolisch\w*|(?:oberer?|unterer?)\s+wert)\b", re.IGNORECASE),
    "sugar": re.compile(
        r"\b(?:blood\s*sugar|blood\s*glucose|sugar|glucose|glukose|blutzucker\w*|zucker\w*|bz"
        r"|mg\s*/\s*dl|mmol\w*)\b", re.IGNORECASE),
    "weight": re.compile(
        r"\b(?:weigh\w*|weight|kilos?|kilograms?|kg|pounds?|lbs?|gewicht\w*|wiege\w*|wog"
        r"|kilogramm)\b", re.IGNORECASE),
    "sleep": re.compile(r"\b(?:sleep\w*|slept|asleep|schlaf\w*|geschlafen)\b", re.IGNORECASE),
}
# Stricter: the agent asked about the vital itself (not e.g. "chest pressure").
_ASKED_RES = {
    "bp": re.compile(r"\b(?:blood\s*pressure|bp|blutdruck\w*)\b", re.IGNORECASE),
    "sugar": re.compile(r"\b(?:blood\s*sugar|blood\s*glucose|glucose|glukose|sugar|blutzucker\w*"
                        r"|zucker(?:wert|spiegel)?)\b", re.IGNORECASE),
    "weight": re.compile(r"\b(?:weigh|weight|gewicht|wiegen)\b", re.IGNORECASE),
}
# Agent follow-ups that continue the previous topic ("What was the reading?").
_FOLLOWUP_RE = re.compile(
    r"\b(?:what\s+was\s+(?:it|that)|reading|number|value|result|how\s+(?:high|low|much)"
    r"|wert|messwert|ergebnis|wie\s+(?:hoch|viel))\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"(?<![\d.,])(\d{1,4})(?:([.,])(\d{1,2}))?(?!\d)")
_HALF_AFTER_RE = re.compile(r"^\s*(?:and\s+a\s+half|einhalb|½)", re.IGNORECASE)

_GUARDED_FIELDS = ("bp_systolic", "bp_diastolic", "blood_sugar", "weight_kg", "sleep_hours")
_FIELD_TOPIC = {"bp_systolic": "bp", "bp_diastolic": "bp", "blood_sugar": "sugar",
                "weight_kg": "weight", "sleep_hours": "sleep"}
_TOPIC_ITEM = {"bp": "blood_pressure", "sugar": "blood_sugar", "weight": "weight", "sleep": "sleep"}
_LBS_TO_KG = 0.45359237


@dataclass
class _Evidence:
    numbers: dict[str, list[float]]  # topic -> numbers the patient said on that topic
    asked: set[str]                  # vitals the agent asked about (bp | sugar | weight)


def _turns(transcript: str) -> list[tuple[bool, str]]:
    """[(is_patient, text)]; unlabelled text counts as patient speech."""
    if not transcripts.has_role_labels(transcript):
        return [(True, transcript)] if transcript.strip() else []
    turns: list[tuple[bool, str]] = []
    for line in transcript.splitlines():
        m = _ROLE_LINE_RE.match(line)
        if m:
            turns.append((m.group(1).lower() in _PATIENT_ROLES, m.group(2).strip()))
        elif line.strip() and turns:
            is_patient, text = turns[-1]
            turns[-1] = (is_patient, f"{text} {line.strip()}".strip())
    return turns


def _numbers(text: str) -> list[float]:
    out: list[float] = []
    for m in _NUMBER_RE.finditer(text):
        whole, sep, frac = m.groups()
        if sep:
            out.append(float(f"{whole}.{frac}"))
            if sep == ",":  # "7,5" (German decimal) or "140,90" (a list)
                out += [float(whole), float(frac)]
        else:
            out.append(float(whole))
            if _HALF_AFTER_RE.match(text[m.end():]):
                out.append(float(whole) + 0.5)
    return out


def _collect_evidence(transcript: str) -> _Evidence:
    numbers: dict[str, list[float]] = {topic: [] for topic in _TOPIC_RES}
    asked: set[str] = set()
    pending: set[str] = set()
    prev_topics: set[str] = set()
    followup = agent_spoke = False
    for is_patient, raw in _turns(transcript):
        text = redflags.normalize_spoken_numbers(raw)
        if not is_patient:
            pending |= {t for t, rx in _TOPIC_RES.items() if rx.search(text)}
            asked |= {t for t, rx in _ASKED_RES.items() if rx.search(text)}
            followup = followup or bool(_FOLLOWUP_RE.search(text))
            agent_spoke = True
            continue
        topics = {t for t, rx in _TOPIC_RES.items() if rx.search(text)} | pending
        if redflags.bp_readings(text):
            topics.add("bp")
        if not topics and (followup or not agent_spoke):
            topics = set(prev_topics)  # "Patient: Yes." / "Agent: What was it?" / "Patient: 130."
        found = _numbers(text)
        for topic in topics:
            numbers[topic] += found
        prev_topics, pending, followup, agent_spoke = topics, set(), False, False
    return _Evidence(numbers=numbers, asked=asked)


def _supported(field_name: str, value: float, evidence: _Evidence) -> bool:
    said = evidence.numbers[_FIELD_TOPIC[field_name]]
    v = float(value)
    tolerance = 0.01 if field_name == "sleep_hours" else 0.5
    if any(abs(n - v) <= tolerance for n in said):
        return True
    if field_name == "blood_sugar":  # stated in mmol/L, stored in mg/dL
        return any(n < 35 and abs(n * redflags.MMOL_TO_MG_DL - v) <= 2.0 for n in said)
    if field_name == "weight_kg":  # stated in pounds, stored in kg
        return any(abs(n * _LBS_TO_KG - v) <= 0.6 for n in said)
    return False


def _canonical_item(raw) -> str | None:
    t = re.sub(r"[^a-zäöüß]+", " ", str(raw or "").lower()).strip()
    if not t:
        return None
    if re.search(r"\b(?:blood pressure|bp|pressure|blutdruck|systolic|diastolic)\b", t):
        return "blood_pressure"
    if re.search(r"\b(?:sugar|glucose|glukose|blutzucker|zucker)\b", t):
        return "blood_sugar"
    if re.search(r"\b(?:weight|weigh|gewicht|kg)\b", t):
        return "weight"
    if re.search(r"\b(?:sleep\w*|schlaf\w*)\b", t):
        return "sleep"
    if re.search(r"\b(?:medication\w*|meds?|medicine|pills?|tablets?|medikament\w*|adherence)\b", t):
        return "medication"
    return None


def _is_captured(cap: CapturedData, item: str) -> bool:
    return {
        "blood_pressure": cap.bp_systolic is not None and cap.bp_diastolic is not None,
        "blood_sugar": cap.blood_sugar is not None,
        "weight": cap.weight_kg is not None,
        "sleep": cap.sleep_hours is not None,
        "medication": cap.meds_taken is not None,
    }[item]


_ABBREVIATIONS = {"dr", "mr", "mrs", "ms", "prof", "vs", "approx", "ca", "e.g", "i.e", "z.b",
                  "bzw", "inkl", "pt", "nr"}
_SENTENCE_END_RE = re.compile(r"[.!?]+(?=\s|$)")


def _limit_sentences(text: str, n: int = 3) -> str:
    text = " ".join((text or "").split())
    count = 0
    for m in _SENTENCE_END_RE.finditer(text):
        word = text[:m.start()].rsplit(" ", 1)[-1].lower()
        if m.group(0) == "." and word in _ABBREVIATIONS:
            continue
        count += 1
        if count == n:
            return text[:m.end()]
    return text


def finalize_report_draft(draft: CallReportDraft, transcript: str) -> tuple[CallReportDraft, list[str]]:
    """Deterministic safety pass over a model draft. Returns (draft, notes).

    - Extraction guard: every numeric captured value must match a number the
      PATIENT said while that vital was discussed (digits or number words;
      mmol/L and pounds conversions allowed). Otherwise it becomes null and
      its item goes to missing_items. Nothing is ever filled in.
    - A vital the agent asked about but that has no value is a missing item.
    - missing_items: canonical names only, de-duplicated, canonical order.
    - risk_flags += "redflag:<category>" for every deterministic red-flag hit.
    - summary trimmed to at most 3 sentences.
    """
    notes: list[str] = []
    evidence = _collect_evidence(transcript or "")
    cap = draft.captured.model_copy(deep=True)
    missing = {c for c in (_canonical_item(i) for i in cap.missing_items) if c}

    for name in _GUARDED_FIELDS:
        value = getattr(cap, name)
        if value is not None and not _supported(name, value, evidence):
            setattr(cap, name, None)
            missing.add(_TOPIC_ITEM[_FIELD_TOPIC[name]])
            notes.append(f"extraction guard: {name}={_fmt(value)} was not stated by the patient; "
                         "set to null")
    if cap.bp_systolic is not None and cap.bp_diastolic is not None \
            and cap.bp_systolic < cap.bp_diastolic:
        cap.bp_systolic, cap.bp_diastolic = cap.bp_diastolic, cap.bp_systolic
        notes.append("extraction guard: systolic/diastolic were swapped; corrected")
    missing |= {_TOPIC_ITEM[t] for t in evidence.asked}
    if (cap.bp_systolic is None) != (cap.bp_diastolic is None):
        missing.add("blood_pressure")  # half a reading is not a reading
    cap.missing_items = [i for i in CANONICAL_MISSING_ITEMS
                         if i in missing and not _is_captured(cap, i)]

    hits = redflags.scan(transcript or "", cap)
    flags = _unique([f.strip() for f in draft.risk_flags if f and f.strip()]
                    + [f"redflag:{h.category}" for h in hits])
    summary = _limit_sentences(draft.summary, 3)
    return draft.model_copy(update={"captured": cap, "risk_flags": flags, "summary": summary}), notes


def _pipeline() -> tuple[str, list[str]]:
    value = (settings.clinical_pipeline or "").strip().lower()
    if value in ("two_stage", "single"):
        return value, []
    return "two_stage", [f"unknown CLINICAL_PIPELINE {value!r}; used two_stage"]


async def generate_report_detailed(transcript: str, patient: PatientContext,
                                   last_reports: list[ReportSummary]) -> GeneratedReport:
    pipeline, notes = _pipeline()
    models: list[str] = []
    now = _local_now()
    context = render_patient_context(patient, last_reports or [], now=now)
    header = f"Current date and time: {describe_now(now)}.\n\nPATIENT CONTEXT\n{context}\n\n"
    transcript_block = ("TRANSCRIPT (Agent = Guardian AI assistant, Patient = the patient)\n"
                        f"{transcript}")

    analysis: str | None = None
    if pipeline == "two_stage":
        model = settings.gemma_model
        try:
            text = await gemma_client.chat(
                model, _messages(REPORT_ANALYSIS_SYSTEM_PROMPT, header + transcript_block),
                temperature=0.2, timeout=settings.model_timeout_s)
            analysis = (text or "").strip() or None
            if analysis:
                models.append(model)
            else:
                notes.append(f"stage 1 ({model}) returned no text; continued as single pass")
        except Exception as exc:  # noqa: BLE001 - stage 1 is optional
            log.warning("report stage 1 (%s) failed: %s; continuing single pass",
                        model, type(exc).__name__)
            notes.append(f"stage 1 ({model}) failed: {type(exc).__name__}; "
                         "continued as single pass")

    analysis_block = ("CLINICAL ANALYSIS (stage 1, from the model; it may contain mistakes, "
                      f"the transcript is the source of truth)\n{analysis}\n\n") if analysis else ""
    draft = await _chat_json(
        CallReportDraft,
        _messages(REPORT_DRAFT_SYSTEM_PROMPT, header + analysis_block + transcript_block),
        "report draft", settings.model_timeout_s)
    models.append(settings.gemma_model)

    draft, guard_notes = finalize_report_draft(draft, transcript)
    notes += guard_notes
    return GeneratedReport(draft=draft, clinical_analysis=analysis,
                           model_info={"pipeline": pipeline, "models": models, "notes": notes})


async def generate_report(transcript: str, patient: PatientContext,
                          last_reports: list[ReportSummary]) -> CallReportDraft:
    return (await generate_report_detailed(transcript, patient, last_reports)).draft


# --- triage ----------------------------------------------------------------------

def _consistent_action(urgency: str, action: str) -> str:
    if urgency == "emergency" and action in ("none", "request_appointment"):
        return "ambulance"
    if urgency == "urgent" and action in ("none", "request_appointment"):
        return "doctor_callback"
    return action


def _rules_only(rule: str, rule_hits: list[str], extra_uncertain: list[str]) -> TriageOutcome:
    urgency = schemas.max_urgency(rule, "monitor")
    urgent = schemas.URGENCY_ORDER[urgency] >= schemas.URGENCY_ORDER["urgent"]
    if rule_hits:
        rationale = (f"Rules-only triage (model unavailable). Red-flag rules matched: "
                     f"{'; '.join(rule_hits)}; minimum urgency '{urgency}'.")
    else:
        rationale = ("Rules-only triage (model unavailable). No red-flag phrase or reading "
                     "matched; marked 'monitor' so a doctor reviews this call.")
    result = TriageResult(urgency=urgency, recommended_action="doctor_callback" if urgent else "none",
                          rationale=rationale,
                          uncertain_because=_unique([MODEL_UNAVAILABLE_NOTE, NO_CHART_NOTE,
                                                     *extra_uncertain]))
    return TriageOutcome(result=result, rule_hits=rule_hits, llm_urgency=None)


def _combine(rule: str, rule_hits: list[str], llm: TriageResult,
             extra_uncertain: list[str]) -> TriageOutcome:
    final = schemas.max_urgency(rule, llm.urgency)  # the LLM can never lower a rule hit
    action = _consistent_action(final, llm.recommended_action)
    parts = []
    if schemas.URGENCY_ORDER[final] > schemas.URGENCY_ORDER[llm.urgency]:
        parts.append(f"Red-flag rules ({'; '.join(rule_hits)}) set a minimum urgency of "
                     f"'{final}'; the model suggested '{llm.urgency}'.")
    elif rule_hits:
        parts.append(f"Red-flag rules matched: {'; '.join(rule_hits)}.")
    if llm.rationale.strip():
        parts.append(llm.rationale.strip())
    if action != llm.recommended_action:
        parts.append(f"Action set to '{action}' to match urgency '{final}' "
                     f"(model suggested '{llm.recommended_action}').")
    uncertain = _unique([NO_CHART_NOTE, *extra_uncertain,
                         *(u.strip() for u in llm.uncertain_because if u and u.strip())])
    result = TriageResult(urgency=final, recommended_action=action, rationale=" ".join(parts),
                          uncertain_because=uncertain)
    return TriageOutcome(result=result, rule_hits=rule_hits, llm_urgency=llm.urgency)


def _triage_messages(report: CallReportDraft | None, transcript: str, patient: PatientContext,
                     rule_hits: list[str], now: datetime) -> list[dict]:
    if report is not None:
        captured = report.captured.model_dump(exclude_defaults=True)
        report_block = (f"DRAFT REPORT SUMMARY: {report.summary}\n"
                        f"VALUES THE PATIENT STATED: {captured or 'none'}\n"
                        f"REPORT RISK FLAGS: {', '.join(report.risk_flags) or 'none'}")
    else:
        report_block = "DRAFT REPORT: not available"
    user = (f"Current date and time: {describe_now(now)}.\n\n"
            f"PATIENT CONTEXT\n{render_patient_context(patient, [], now=now)}\n\n"
            "DETERMINISTIC RED-FLAG RULE HITS (any hit means urgency is at least 'urgent'): "
            f"{'; '.join(rule_hits) or 'none'}\n\n{report_block}\n\n"
            f"TRANSCRIPT (Agent = Guardian AI assistant, Patient = the patient)\n{transcript}")
    return _messages(TRIAGE_SYSTEM_PROMPT, user)


async def triage_detailed(report: CallReportDraft | None, transcript: str,
                          patient: PatientContext) -> TriageOutcome:
    extra_uncertain: list[str] = []
    try:
        hits = redflags.scan(transcript or "", report.captured if report is not None else None)
        rule = redflags.rule_urgency(hits)
    except Exception:  # noqa: BLE001 - NEVER raises; a broken net means a human looks
        log.exception("triage: red-flag scan failed")
        hits, rule = [], "monitor"
        extra_uncertain.append(SCAN_FAILED_NOTE)
    rule_hits = [f"{h.category}: {h.matched}" for h in hits]
    try:
        messages = _triage_messages(report, transcript or "", patient, rule_hits, _local_now())
        llm = await _chat_json(TriageResult, messages, "triage", settings.model_timeout_s)
        return _combine(rule, rule_hits, llm, extra_uncertain)
    except Exception as exc:  # noqa: BLE001 - NEVER raises
        log.warning("triage: model unavailable (%s); rules-only result", type(exc).__name__)
        return _rules_only(rule, rule_hits, extra_uncertain)


async def triage(report: CallReportDraft | None, transcript: str,
                 patient: PatientContext) -> TriageResult:
    return (await triage_detailed(report, transcript, patient)).result


_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def propose_risk_level(patient: PatientContext, report: CallReportDraft | None,
                       triage_result: TriageResult) -> str | None:
    """Suggested risk level; never lowers it. None if nothing would change."""
    current = (patient.risk_level or "low").strip().lower()
    rank = _RISK_ORDER.get(current, 0)
    target = {"urgent": "high", "emergency": "high", "monitor": "medium"}.get(triage_result.urgency)
    if target is None or _RISK_ORDER[target] <= rank:
        return None
    return target


# --- doctor's personal agent: call jobs -------------------------------------------

_HONORIFICS = {"mr", "mrs", "ms", "miss", "mister", "dr", "doctor", "prof", "frau", "herr",
               "patient", "patientin", "pt"}
_ID_REF_RE = re.compile(r"^(?:(?:patient|pt|id|nr|no|number|nummer)\s*)?#?\s*(\d+)$", re.IGNORECASE)
MATCH_MIN_SCORE = 0.75  # every word of the reference must match a name word this well
MATCH_MARGIN = 0.1      # ... and the best patient must beat the runner-up by this much


def _name_tokens(name: str) -> list[str]:
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return [t for t in re.sub(r"[^\w\s]", " ", s).split() if t not in _HONORIFICS]


def _token_score(token: str, name_tokens: list[str]) -> float:
    if len(token) == 1:  # an initial: "Maria G."
        return 1.0 if any(t.startswith(token) for t in name_tokens) else 0.0
    return max(difflib.SequenceMatcher(None, token, t).ratio() for t in name_tokens)


def _name_scores(ref_tokens: list[str], name_tokens: list[str]) -> tuple[float, float]:
    """(strict, lenient): weakest / average word match. Strict decides,
    lenient only ranks suggestions."""
    if not ref_tokens or not name_tokens:
        return 0.0, 0.0
    scores = [_token_score(t, name_tokens) for t in ref_tokens]
    return min(scores), sum(scores) / len(scores)


def _candidate(p: dict, score: float) -> dict:
    return {"id": p["id"], "name": p["name"], "score": round(score, 2)}


def _clarify_between(patients: list[dict]) -> str:
    names = [p["name"] for p in patients]
    labels = [f"{p['name']} (#{p['id']})" if names.count(p["name"]) > 1 else p["name"]
              for p in patients]
    return f"Did you mean {_join_names(labels, 'or')}?"


def resolve_patient_ref(ref: str | None, doctors_patients: list[dict]) -> PatientResolution:
    """Match a spoken/typed reference against the doctor's own patients only.

    Numeric id, exact case-insensitive name, then difflib similarity per name
    word (first/last name, initials). Resolves only when the best match is
    clearly better than the runner-up; otherwise asks instead of guessing.
    """
    patients = [p for p in doctors_patients or [] if p.get("id") is not None and p.get("name")]
    ref = " ".join((ref or "").split())
    if not ref:
        return PatientResolution(clarification="Which patient should Guardian call?")

    id_match = _ID_REF_RE.match(ref)
    if id_match:
        pid = int(id_match.group(1))
        found = [p for p in patients if int(p["id"]) == pid]
        if found:
            return PatientResolution(patient_id=int(found[0]["id"]))
        return PatientResolution(clarification=f"I couldn't find patient #{pid} among your patients.")

    ref_tokens = _name_tokens(ref)
    exact = [p for p in patients if _name_tokens(p["name"]) == ref_tokens]
    if len(exact) == 1:
        return PatientResolution(patient_id=int(exact[0]["id"]))
    if len(exact) > 1:
        return PatientResolution(clarification=_clarify_between(exact),
                                 candidates=[_candidate(p, 1.0) for p in exact])

    scored = sorted(((*_name_scores(ref_tokens, _name_tokens(p["name"])), p) for p in patients),
                    key=lambda x: (x[0], x[1]), reverse=True)
    if scored and scored[0][0] >= MATCH_MIN_SCORE:
        best = scored[0][0]
        close = [(s, p) for s, _, p in scored if s >= MATCH_MIN_SCORE and best - s < MATCH_MARGIN]
        if len(close) == 1:
            return PatientResolution(patient_id=int(close[0][1]["id"]))
        close = close[:5]
        return PatientResolution(clarification=_clarify_between([p for _, p in close]),
                                 candidates=[_candidate(p, s) for s, p in close])

    suggestions = sorted(((lenient, p) for _, lenient, p in scored if lenient >= 0.5),
                         key=lambda x: x[0], reverse=True)[:3]
    return PatientResolution(clarification=f"I couldn't find '{ref}' among your patients.",
                             candidates=[_candidate(p, s) for s, p in suggestions])


def parse_scheduled_at(value: str | None) -> str | None:
    """ISO 8601 (naive = clinic-local) -> naive-UTC ISO string, else None.
    A date without a time of day is not schedulable and also yields None."""
    s = (value or "").strip()
    if not s or not re.search(r"\d{1,2}:\d{2}", s):
        return None
    if s[-1] in "Zz":
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return timeutil.to_utc_naive(dt).isoformat()


def _call_job_messages(text: str, patients: list[dict], now: datetime) -> list[dict]:
    days = []
    for i in range(1, 8):
        d = now + timedelta(days=i)
        days.append(f"{_WEEKDAYS[d.weekday()]} {d:%Y-%m-%d}" + (" (tomorrow)" if i == 1 else ""))
    roster = "\n".join(f"- id {p['id']}: {p['name']}" for p in patients) or "- (no patients)"
    user = (f"Current local date and time: {describe_now(now)}.\n"
            f"Today is {_WEEKDAYS[now.weekday()]} {now:%Y-%m-%d}. Next days: {', '.join(days)}.\n\n"
            f"THE DOCTOR'S PATIENTS\n{roster}\n\n"
            f'DOCTOR\'S MESSAGE\n"""\n{text.strip()}\n"""')
    return _messages(CALL_JOB_SYSTEM_PROMPT, user)


async def extract_call_job(text: str, doctors_patients: list[dict], *,
                           now: datetime | None = None) -> CallJobExtraction:
    if not (text or "").strip():
        return CallJobExtraction(clarification="Which patient should Guardian call, and what should it ask?")
    now = _local_now(now)
    patients = [p for p in doctors_patients or [] if p.get("id") is not None and p.get("name")]
    draft = await _chat_json(CallJobDraft, _call_job_messages(text, patients, now),
                             "call job extraction", settings.model_timeout_s)
    draft = draft.model_copy(update={"questions": [q.strip() for q in draft.questions
                                                   if q and q.strip()]})
    match = resolve_patient_ref(draft.patient_ref, patients)
    if match.patient_id is None:
        return CallJobExtraction(clarification=match.clarification, candidates=match.candidates)
    return CallJobExtraction(draft=draft, patient_id=match.patient_id,
                             scheduled_at_utc=parse_scheduled_at(draft.scheduled_at))


# --- Phase 2 stub -------------------------------------------------------------------

async def compare_images(prior_b64: str, new_b64: str, context: str) -> str:
    """Phase 2 stub: recovery comparison between two uploads (X-ray / wound
    photo) with Gemma vision via images_b64. Doctor-facing draft text only.
    No route and no UI are built for it yet.
    Raises ModelError if the model is unavailable."""
    user = (f"Clinical context: {context or 'none given'}\n"
            "Image 1 is the earlier upload, image 2 is the new upload. Compare them.")
    try:
        return await gemma_client.chat(settings.gemma_model,
                                       _messages(IMAGE_COMPARE_SYSTEM_PROMPT, user),
                                       temperature=0.2, timeout=settings.model_timeout_s,
                                       images_b64=[prior_b64, new_b64])
    except Exception as exc:
        err = _as_model_error(exc, "image comparison")
        if err is exc:
            raise
        raise err from exc
