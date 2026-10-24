"""context_builder: patient context, agent memory, doctor questions and the
ElevenLabs dynamic variables (ELEVENLABS_TWILIO_SETUP.md Step 2,
MEDGEMMA_DO_SETUP.md 6.2 inputs).

The clinical service is implemented in parallel, so every test that builds
dynamic variables monkeypatches it (see FakeClinical).
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta

import pytest

from app import models
from app.adapters import elevenlabs
from app.config import settings
from app.schemas.clinical import PlannedQuestion, QuestionPlan
from app.schemas.context import PatientContext, ReportSummary
from app.services import clinical
from app.services import context_builder as cb
from app.timeutil import now_local, to_local, utcnow

REQUIRED = set(elevenlabs.REQUIRED_DYNAMIC_VARIABLES)
MARIA_TEMPLATE = [
    "Did you take your blood pressure today?",
    "Did you check your blood sugar? What was the reading?",
    "Did you take your medication today, and at what time?",
]


class FakeClinical:
    """Stands in for app.services.clinical (build/fallback/render)."""

    def __init__(self) -> None:
        self.plan_calls: list[tuple[PatientContext, list[ReportSummary], list[str]]] = []
        self.fallback_calls: list[list[str]] = []
        self.plan_error: Exception | None = None
        self.plan_delay_s = 0.0

    async def build_question_plan(self, patient, last_reports, doctor_questions):
        self.plan_calls.append((patient, last_reports, list(doctor_questions)))
        if self.plan_delay_s:
            await asyncio.sleep(self.plan_delay_s)
        if self.plan_error is not None:
            raise self.plan_error
        return QuestionPlan(questions=[
            *(PlannedQuestion(id=f"d{i}", text=q, source="doctor")
              for i, q in enumerate(doctor_questions, 1)),
            PlannedQuestion(id="rf", text=clinical.DEFAULT_REDFLAG_PROBE, source="redflag_probe"),
        ])

    def fallback_plan(self, doctor_questions):
        self.fallback_calls.append(list(doctor_questions))
        return QuestionPlan(questions=[
            PlannedQuestion(id=f"s{i}", text=q, source="doctor")
            for i, q in enumerate(doctor_questions, 1)
        ])

    @staticmethod
    def render_plan_for_agent(plan):
        # Distinctive format so tests can tell it apart from the local fallback.
        return "\n".join(f"Q{i} [{q.source}] {q.text}" for i, q in enumerate(plan.questions, 1))


@pytest.fixture
def fake_clinical(monkeypatch):
    fake = FakeClinical()
    monkeypatch.setattr(clinical, "build_question_plan", fake.build_question_plan)
    monkeypatch.setattr(clinical, "fallback_plan", fake.fallback_plan)
    monkeypatch.setattr(clinical, "render_plan_for_agent", fake.render_plan_for_agent)
    return fake


def _session(db, patient_id, **kw):
    s = models.CallSession(patient_id=patient_id, direction=kw.pop("direction", "outbound"),
                           channel="voice", status=kw.pop("status", "scheduled"), **kw)
    db.add(s)
    db.flush()
    return s


def _report(db, patient_id, *, days_ago: float, summary: str | None, status: str = "ok",
            risk_flags=None, captured=None):
    s = _session(db, patient_id, status="completed")
    r = models.CallReport(call_session_id=s.id, patient_id=patient_id, status=status,
                          summary=summary, risk_flags=risk_flags or [],
                          structured_data=captured,
                          created_at=utcnow() - timedelta(days=days_ago))
    db.add(r)
    db.flush()
    return r


# --- load_patient_context ----------------------------------------------------

def test_patient_context_maps_record_and_medications(db, seeded):
    ctx = cb.load_patient_context(db, seeded["maria_id"])

    assert isinstance(ctx, PatientContext)
    assert ctx.id == seeded["maria_id"]
    assert ctx.name == "Maria Gomez"
    assert ctx.phone == "+15550000001"
    assert ctx.category == "post-op"
    assert ctx.risk_level == "high"
    assert ctx.chronic_conditions == ["hypertension", "type 2 diabetes"]
    assert [(m.name, m.dose, m.schedule, m.is_new_prescription) for m in ctx.medications] == [
        ("Lisinopril", "10mg", "once daily", True),
        ("Metformin", "500mg", "twice daily", False),
    ]
    assert ctx.has_new_prescription is True


def test_vitals_trend_is_last_seven_days_oldest_first(db, seeded):
    maria, john = seeded["maria_id"], seeded["john_id"]
    now = utcnow()
    db.add_all([
        models.Vital(patient_id=maria, type="weight", value=80, unit="kg",
                     recorded_at=now - timedelta(days=8)),  # outside the window
        models.Vital(patient_id=maria, type="bp_systolic", value=141, unit="mmHg",
                     recorded_at=now - timedelta(hours=3)),
        models.Vital(patient_id=maria, type="weight", value=78.5, unit="kg",
                     recorded_at=now - timedelta(days=3)),
        models.Vital(patient_id=john, type="bp_systolic", value=200, unit="mmHg",
                     recorded_at=now - timedelta(days=1)),  # someone else's reading
    ])
    db.commit()

    trend = cb.load_patient_context(db, maria).vitals_trend

    assert [(v.type, v.value) for v in trend] == [
        ("bp_systolic", 158), ("bp_diastolic", 95),  # seeded, 6 days ago
        ("weight", 78.5),                             # 3 days ago
        ("blood_sugar", 180),                         # seeded, 1 day ago
        ("bp_systolic", 141),                         # 3 hours ago
    ]
    stamps = [v.recorded_at for v in trend]
    assert stamps == sorted(stamps)
    assert all(t.tzinfo is None and t >= now - timedelta(days=7) for t in stamps)
    assert trend[0].unit == "mmHg"


def test_patient_without_history_has_empty_context(db, seeded):
    ctx = cb.load_patient_context(db, seeded["john_id"])
    assert ctx.name == "John Carter"
    assert ctx.medications == [] and ctx.vitals_trend == []
    assert ctx.has_new_prescription is False


@pytest.mark.parametrize("patient_id", [999_999, None])
def test_unknown_patient_raises_lookup_error(db, seeded, patient_id):
    with pytest.raises(LookupError):
        cb.load_patient_context(db, patient_id)


# --- load_last_reports -------------------------------------------------------

def test_last_reports_newest_first_limited_and_skip_failed(db, seeded, monkeypatch):
    maria = seeded["maria_id"]
    _report(db, maria, days_ago=1, summary="R1", risk_flags=["x"], captured={"bp_systolic": 140})
    _report(db, maria, days_ago=2, summary="FAILED", status="failed")
    _report(db, maria, days_ago=3, summary="R3", status="partial")
    _report(db, maria, days_ago=10, summary="R10")
    _report(db, seeded["john_id"], days_ago=0.5, summary="JOHN")
    db.commit()
    seeded_summary = "BP high (158/95). Reports occasional dizziness. Mild cough."

    assert settings.history_reports_n == 3
    reports = cb.load_last_reports(db, maria)
    assert [r.summary for r in reports] == ["R1", "R3", seeded_summary]
    assert reports[0].risk_flags == ["x"]
    assert reports[0].captured == {"bp_systolic": 140}
    assert reports[0].id is not None and reports[0].created_at.tzinfo is None
    assert reports[2].captured == {"bp_systolic": 158, "bp_diastolic": 95, "symptoms": ["cough"]}

    assert [r.summary for r in cb.load_last_reports(db, maria, n=2)] == ["R1", "R3"]
    assert [r.summary for r in cb.load_last_reports(db, maria, n=10)] == [
        "R1", "R3", seeded_summary, "R10"]
    assert cb.load_last_reports(db, maria, n=0) == []
    monkeypatch.setattr(settings, "history_reports_n", 1)
    assert [r.summary for r in cb.load_last_reports(db, maria)] == ["R1"]


def test_last_reports_empty_for_patient_without_calls(db, seeded):
    assert cb.load_last_reports(db, seeded["john_id"]) == []


# --- doctor_questions_for ----------------------------------------------------

def test_patient_template_wins_over_defaults(db, seeded):
    db.add(models.QuestionTemplate(doctor_id=seeded["doctor_id"], patient_id=None,
                                   questions=["Doctor default question?"]))
    db.commit()
    assert cb.doctor_questions_for(db, seeded["maria_id"]) == MARIA_TEMPLATE


def test_no_template_falls_back_to_question_bank(db, seeded):
    assert cb.doctor_questions_for(db, seeded["john_id"]) == cb.DEFAULT_DOCTOR_QUESTIONS


def test_doctor_default_template_used_without_patient_template(db, seeded):
    other = models.Doctor(name="Dr. Other", email="other@example.com")
    db.add(other)
    db.flush()
    db.add_all([
        models.QuestionTemplate(doctor_id=other.id, patient_id=None,
                                questions=["Other doctor's question?"]),
        models.QuestionTemplate(doctor_id=seeded["doctor_id"], patient_id=None,
                                questions=["What's your current weight?",
                                           "How are you feeling overall today?"]),
        # An empty personal template counts as "no template".
        models.QuestionTemplate(doctor_id=seeded["doctor_id"], patient_id=seeded["john_id"],
                                questions=[]),
    ])
    db.commit()
    assert cb.doctor_questions_for(db, seeded["john_id"]) == [
        "What's your current weight?", "How are you feeling overall today?"]


def test_call_job_questions_first_then_template_deduplicated(db, seeded):
    job = models.CallJob(doctor_id=seeded["doctor_id"], patient_id=seeded["maria_id"],
                         questions=[
                             "How is the wound healing?",
                             "  did you take your BLOOD   pressure today  ",  # dup of template
                             "How is the wound healing",                     # dup of job q1
                             "",
                         ])
    db.add(job)
    db.commit()

    assert cb.doctor_questions_for(db, seeded["maria_id"], job.id) == [
        "How is the wound healing?",
        "did you take your BLOOD pressure today",  # first wording wins, whitespace tidied
        "Did you check your blood sugar? What was the reading?",
        "Did you take your medication today, and at what time?",
    ]


def test_template_duplicates_are_removed(db, seeded):
    db.add(models.QuestionTemplate(doctor_id=seeded["doctor_id"], patient_id=seeded["john_id"],
                                   questions=["Any side effects?", "any side effects ?",
                                              "Any  side effects?", "How did you sleep?"]))
    db.commit()
    assert cb.doctor_questions_for(db, seeded["john_id"]) == ["Any side effects?",
                                                               "How did you sleep?"]


def test_foreign_or_missing_call_job_is_ignored(db, seeded):
    johns_job = models.CallJob(doctor_id=seeded["doctor_id"], patient_id=seeded["john_id"],
                               questions=["John's private question?"])
    db.add(johns_job)
    db.commit()
    assert cb.doctor_questions_for(db, seeded["maria_id"], johns_job.id) == MARIA_TEMPLATE
    assert cb.doctor_questions_for(db, seeded["maria_id"], 424242) == MARIA_TEMPLATE


# --- last_call_summary -------------------------------------------------------

def test_last_call_summary_none_without_reports():
    assert cb.last_call_summary([]) == "none"
    assert cb.last_call_summary([ReportSummary(created_at=utcnow(), summary="  ")]) == "none"


def test_last_call_summary_uses_local_date():
    year = now_local().year
    # 23:30 UTC on 17 Jan is already 18 Jan in Europe/Berlin (UTC+1).
    report = ReportSummary(created_at=datetime(year, 1, 17, 23, 30), summary="BP improved.")
    assert cb.last_call_summary([report]) == "On 18 Jan: BP improved."
    # Summer time (UTC+2), and an older year gets the year spelled out.
    old = ReportSummary(created_at=datetime(year - 1, 10, 17, 22, 15),
                        summary="Mild\n cough,   no fever.")
    assert cb.last_call_summary([old]) == f"On 18 Oct {year - 1}: Mild cough, no fever."


def test_last_call_summary_picks_most_recent_with_summary():
    now = utcnow()
    reports = [  # deliberately not newest-first
        ReportSummary(created_at=now - timedelta(days=9), summary="Oldest."),
        ReportSummary(created_at=now - timedelta(days=1), summary=None),  # partial, no summary
        ReportSummary(created_at=now - timedelta(days=4), summary="Middle."),
    ]
    expected_day = to_local(now - timedelta(days=4)).day
    assert cb.last_call_summary(reports).startswith(f"On {expected_day} ")
    assert cb.last_call_summary(reports).endswith(": Middle.")
    assert cb.last_call_summary([ReportSummary(summary="Undated.")]) == "Undated."


def test_last_call_summary_is_capped():
    text = cb.last_call_summary([ReportSummary(summary="word " * 400)])
    assert len(text) <= cb.SUMMARY_MAX_CHARS and text.endswith("...")


def test_last_call_summary_from_seeded_history(db, seeded):
    reports = cb.load_last_reports(db, seeded["maria_id"])
    local = to_local(reports[0].created_at)
    month = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")[local.month - 1]
    year = "" if local.year == now_local().year else f" {local.year}"
    assert cb.last_call_summary(reports) == (
        f"On {local.day} {month}{year}: "
        "BP high (158/95). Reports occasional dizziness. Mild cough.")


# --- build_dynamic_variables -------------------------------------------------

async def test_dynamic_variables_have_exactly_the_required_string_keys(db, seeded, fake_clinical):
    session = _session(db, seeded["maria_id"])

    variables, plan = await cb.build_dynamic_variables(db, session)

    assert set(variables) == REQUIRED and len(variables) == 5
    assert all(isinstance(v, str) and v for v in variables.values())
    assert variables["patient_name"] == "Maria Gomez"
    assert variables["session_id"] == str(session.id)
    assert variables["resume_from"] == "start"
    assert variables["last_call_summary"].endswith("Mild cough.")
    assert variables["last_call_summary"].startswith("On ")
    assert variables["question_plan"] == FakeClinical.render_plan_for_agent(plan)
    assert plan.questions[-1].source == "redflag_probe"

    # The plan was built from the DB context (MEDGEMMA_DO_SETUP.md 6.2 inputs).
    [(patient, reports, questions)] = fake_clinical.plan_calls
    assert patient.id == seeded["maria_id"] and patient.has_new_prescription
    assert len(patient.vitals_trend) == 3
    assert [r.summary for r in reports] == [
        "BP high (158/95). Reports occasional dizziness. Mild cough."]
    assert questions == MARIA_TEMPLATE
    assert fake_clinical.fallback_calls == []


async def test_dynamic_variables_for_patient_without_history(db, seeded, fake_clinical):
    session = _session(db, seeded["john_id"])
    variables, _ = await cb.build_dynamic_variables(db, session)
    assert variables["patient_name"] == "John Carter"
    assert variables["last_call_summary"] == "none"
    assert fake_clinical.plan_calls[0][2] == cb.DEFAULT_DOCTOR_QUESTIONS


async def test_dynamic_variables_resume_from_and_call_job(db, seeded, fake_clinical):
    job = models.CallJob(doctor_id=seeded["doctor_id"], patient_id=seeded["maria_id"],
                         questions=["Is the new knee brace comfortable?"])
    db.add(job)
    db.flush()
    session = _session(db, seeded["maria_id"], call_job_id=job.id, status="callback_pending")

    variables, _ = await cb.build_dynamic_variables(
        db, session, resume_from="  Asked meds and sleep; patient went to take BP.  ")
    assert variables["resume_from"] == "Asked meds and sleep; patient went to take BP."
    assert fake_clinical.plan_calls[0][2][0] == "Is the new knee brace comfortable?"
    assert fake_clinical.plan_calls[0][2][1:] == MARIA_TEMPLATE

    for blank in ("", "   ", None):
        variables, _ = await cb.build_dynamic_variables(db, session, resume_from=blank)
        assert variables["resume_from"] == "start"


async def test_falls_back_to_static_plan_when_plan_raises(db, seeded, fake_clinical):
    fake_clinical.plan_error = RuntimeError("model exploded")
    session = _session(db, seeded["maria_id"])

    variables, plan = await cb.build_dynamic_variables(db, session)

    assert fake_clinical.fallback_calls == [MARIA_TEMPLATE]
    assert [q.text for q in plan.questions] == MARIA_TEMPLATE
    assert {q.source for q in plan.questions} == {"doctor"}
    assert variables["question_plan"] == FakeClinical.render_plan_for_agent(plan)
    assert set(variables) == REQUIRED


async def test_falls_back_when_plan_hangs_past_budget(db, seeded, fake_clinical, monkeypatch):
    monkeypatch.setattr(settings, "plan_timeout_s", 0.05)
    monkeypatch.setattr(cb, "PLAN_GRACE_S", 0.0)
    fake_clinical.plan_delay_s = 5
    session = _session(db, seeded["maria_id"])

    started = time.monotonic()
    _, plan = await cb.build_dynamic_variables(db, session)

    assert time.monotonic() - started < 2
    assert fake_clinical.fallback_calls == [MARIA_TEMPLATE]
    assert [q.id for q in plan.questions] == ["s1", "s2", "s3"]


async def test_plan_timeout_zero_skips_the_model(db, seeded, fake_clinical):
    variables, plan = await cb.build_dynamic_variables(
        db, _session(db, seeded["maria_id"]), plan_timeout_s=0)
    assert fake_clinical.plan_calls == []
    assert fake_clinical.fallback_calls == [MARIA_TEMPLATE]
    assert set(variables) == REQUIRED
    assert [q.text for q in plan.questions] == MARIA_TEMPLATE


async def test_plan_timeout_override_shortens_the_wait(db, seeded, fake_clinical):
    fake_clinical.plan_delay_s = 5  # well past the 0.05 s budget below
    started = time.monotonic()
    _, plan = await cb.build_dynamic_variables(
        db, _session(db, seeded["maria_id"]), plan_timeout_s=0.05)
    assert time.monotonic() - started < 2
    assert len(fake_clinical.plan_calls) == 1
    assert [q.id for q in plan.questions] == ["s1", "s2", "s3"]


async def test_empty_plan_from_model_uses_fallback(db, seeded, fake_clinical, monkeypatch):
    async def empty_plan(*_args):
        return QuestionPlan(questions=[])
    monkeypatch.setattr(clinical, "build_question_plan", empty_plan)
    _, plan = await cb.build_dynamic_variables(db, _session(db, seeded["maria_id"]))
    assert [q.text for q in plan.questions] == MARIA_TEMPLATE


async def test_survives_unimplemented_clinical_service(db, seeded, monkeypatch):
    def not_ready(*_args, **_kw):
        raise NotImplementedError

    async def not_ready_async(*_args, **_kw):
        raise NotImplementedError

    monkeypatch.setattr(clinical, "build_question_plan", not_ready_async)
    monkeypatch.setattr(clinical, "fallback_plan", not_ready)
    monkeypatch.setattr(clinical, "render_plan_for_agent", not_ready)

    variables, plan = await cb.build_dynamic_variables(db, _session(db, seeded["maria_id"]))

    assert set(variables) == REQUIRED
    assert [q.text for q in plan.questions] == MARIA_TEMPLATE
    assert variables["question_plan"] == "\n".join(
        f"{i}. {q}" for i, q in enumerate(MARIA_TEMPLATE, 1))


async def test_unknown_patient_propagates_lookup_error(db, seeded, fake_clinical):
    session = _session(db, 999_999)
    with pytest.raises(LookupError):
        await cb.build_dynamic_variables(db, session)
    assert fake_clinical.plan_calls == []


async def test_session_without_patient_gets_unknown_caller_defaults(db, fake_clinical):
    session = _session(db, None, direction="inbound")
    variables, plan = await cb.build_dynamic_variables(db, session, resume_from="Asked name.")
    assert variables == {**cb.unknown_caller_variables(session.id), "resume_from": "Asked name."}
    assert plan.questions == []
    assert fake_clinical.plan_calls == []


async def test_unsaved_session_is_flushed_for_its_id(db, seeded, fake_clinical):
    session = models.CallSession(patient_id=seeded["maria_id"], direction="outbound",
                                 channel="voice", status="scheduled")
    variables, _ = await cb.build_dynamic_variables(db, session)
    assert session.id is not None
    assert variables["session_id"] == str(session.id)


# --- unknown_caller_variables ------------------------------------------------

def test_unknown_caller_variables():
    variables = cb.unknown_caller_variables(42)
    assert variables == {
        "patient_name": "caller",
        "question_plan": "Ask how you can help and note their name and issue.",
        "last_call_summary": "none",
        "session_id": "42",
        "resume_from": "start",
    }
    assert set(variables) == REQUIRED
    assert cb.unknown_caller_variables(7, resume_from="Asked the issue.")["resume_from"] == (
        "Asked the issue.")
    assert cb.unknown_caller_variables(7, resume_from=" ")["resume_from"] == "start"
