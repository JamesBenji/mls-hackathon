"""Unit tests for the clinical/context schemas (MEDGEMMA_DO_SETUP.md §6.1, §8).

The clinical classes double as the JSON Schemas sent to Ollama as `format`,
so what they accept or reject is what a model reply is held to.
"""
from __future__ import annotations

import json
from typing import get_args

import pytest
from pydantic import ValidationError

from app.schemas.clinical import (
    URGENCY_ORDER,
    Action,
    CallJobDraft,
    CallJobExtraction,
    CallReportDraft,
    CapturedData,
    PlannedQuestion,
    QuestionPlan,
    Soap,
    TriageResult,
    Urgency,
    max_urgency,
)
from app.schemas.context import MedicationInfo, PatientContext, ReportSummary

URGENCIES = list(get_args(Urgency))
ACTIONS = list(get_args(Action))
SOAP = {"subjective": "Reports mild dizziness.", "objective": "BP 150/92 (patient-reported).",
        "assessment": "BP above target.", "plan": "Doctor to review medication."}


def _report(**overrides) -> CallReportDraft:
    data = {
        "summary": "Patient reported BP 150/92 and took medication at 08:00.",
        "soap": SOAP,
        "captured": {"bp_systolic": 150, "bp_diastolic": 92, "meds_taken": True,
                     "med_time": "08:00", "symptoms": ["dizziness"]},
        "risk_flags": ["high_bp"],
        "changes_since_last_call": ["BP down from 158/95"],
    }
    data.update(overrides)
    return CallReportDraft.model_validate(data)


# --- CapturedData ------------------------------------------------------------------

def test_captured_data_defaults_are_empty():
    captured = CapturedData()

    assert captured.model_dump() == {
        "bp_systolic": None, "bp_diastolic": None, "blood_sugar": None, "weight_kg": None,
        "sleep_hours": None, "meds_taken": None, "med_time": None,
        "side_effects": [], "symptoms": [], "missing_items": [],
    }


def test_captured_data_lists_are_not_shared_between_instances():
    first, second = CapturedData(), CapturedData()
    first.symptoms.append("cough")
    first.side_effects.append("nausea")
    first.missing_items.append("blood_pressure")

    assert second.symptoms == [] and second.side_effects == [] and second.missing_items == []
    assert CapturedData().missing_items == []


@pytest.mark.parametrize("raw", [
    '{"bp_systolic": "high"}',  # words are not numbers
    '{"bp_systolic": 140.5}',  # no fractional mmHg sneaking into an int
    '{"meds_taken": "maybe"}',
    '{"symptoms": "cough"}',  # must be a list
])
def test_captured_data_rejects_wrong_types(raw):
    with pytest.raises(ValidationError):
        CapturedData.model_validate_json(raw)


def test_captured_data_nulls_mean_not_stated():
    captured = CapturedData.model_validate_json(
        '{"bp_systolic": null, "bp_diastolic": null, "missing_items": ["blood_pressure"]}')

    assert captured.bp_systolic is None and captured.bp_diastolic is None
    assert captured.missing_items == ["blood_pressure"]


# --- TriageResult --------------------------------------------------------------------

@pytest.mark.parametrize("urgency", ["critical", "high", "", "URGENT"])
def test_triage_rejects_unknown_urgency(urgency):
    with pytest.raises(ValidationError):
        TriageResult(urgency=urgency, recommended_action="none", rationale="x")


@pytest.mark.parametrize("action", ["call_police", "prescribe", "ambulance_now", ""])
def test_triage_rejects_unknown_action(action):
    with pytest.raises(ValidationError):
        TriageResult(urgency="urgent", recommended_action=action, rationale="x")


def test_triage_rejects_unknown_values_from_json_too():
    with pytest.raises(ValidationError):
        TriageResult.model_validate_json(
            '{"urgency": "critical", "recommended_action": "none", "rationale": "x"}')


@pytest.mark.parametrize("urgency", URGENCIES)
@pytest.mark.parametrize("action", ACTIONS)
def test_triage_accepts_every_known_combination(urgency, action):
    result = TriageResult(urgency=urgency, recommended_action=action, rationale="r")

    assert (result.urgency, result.recommended_action) == (urgency, action)


def test_triage_uncertain_because_defaults_are_not_shared():
    first = TriageResult(urgency="none", recommended_action="none", rationale="r")
    second = TriageResult(urgency="none", recommended_action="none", rationale="r")
    first.uncertain_because.append("no exam")

    assert second.uncertain_because == []


def test_triage_schema_enums_constrain_the_model():
    # This schema is the `format` sent to Ollama: the grammar may only allow
    # the literal values.
    props = TriageResult.model_json_schema()["properties"]

    assert props["urgency"]["enum"] == URGENCIES
    assert props["recommended_action"]["enum"] == ACTIONS


# --- CallReportDraft -----------------------------------------------------------------

def test_call_report_draft_round_trips_through_json_schema_and_json():
    schema = CallReportDraft.model_json_schema()
    draft = _report()

    assert set(schema["required"]) == {
        "summary", "soap", "captured", "risk_flags", "changes_since_last_call"}
    assert {"Soap", "CapturedData"} <= set(schema["$defs"])
    assert json.loads(json.dumps(schema)) == schema  # serialisable as `format`
    again = CallReportDraft.model_validate_json(draft.model_dump_json())
    assert again == draft


def test_call_report_draft_from_model_style_json_fills_defaults():
    raw = json.dumps({
        "summary": "No BP reading available today.",
        "soap": SOAP,
        "captured": {"bp_systolic": None, "missing_items": ["blood_pressure"]},
        "risk_flags": [],
        "changes_since_last_call": [],
    })

    draft = CallReportDraft.model_validate_json(raw)

    assert draft.captured.bp_systolic is None
    assert draft.captured.missing_items == ["blood_pressure"]
    assert draft.captured.symptoms == []


@pytest.mark.parametrize("broken", [
    {"soap": None},
    {"soap": {k: v for k, v in SOAP.items() if k != "plan"}},
    {"risk_flags": "high_bp"},
    {"captured": {"bp_systolic": "one-fifty"}},
])
def test_call_report_draft_rejects_incomplete_output(broken):
    with pytest.raises(ValidationError):
        _report(**broken)


def test_soap_requires_all_sections():
    with pytest.raises(ValidationError):
        Soap(subjective="s", objective="o", assessment="a")


# --- QuestionPlan --------------------------------------------------------------------

@pytest.mark.parametrize("source", ["doctor", "history_followup", "redflag_probe"])
def test_planned_question_accepts_known_sources(source):
    question = PlannedQuestion(id="q1", text="How did you sleep?", source=source)

    assert question.source == source
    assert question.skip_if_answered is True


@pytest.mark.parametrize("source", ["model", "llm", "Doctor", ""])
def test_planned_question_rejects_unknown_source(source):
    with pytest.raises(ValidationError):
        PlannedQuestion(id="q1", text="?", source=source)


def test_question_plan_round_trip_and_schema():
    raw = json.dumps({"questions": [
        {"id": "d1", "text": "Did you take your blood pressure today?", "source": "doctor"},
        {"id": "h1", "text": "Is the cough from last week better?",
         "source": "history_followup", "skip_if_answered": False},
        {"id": "r1", "text": "Any chest pain right now?", "source": "redflag_probe"},
    ]})

    plan = QuestionPlan.model_validate_json(raw)

    assert [q.source for q in plan.questions] == ["doctor", "history_followup", "redflag_probe"]
    assert plan.questions[1].skip_if_answered is False
    assert QuestionPlan.model_validate_json(plan.model_dump_json()) == plan
    source_schema = QuestionPlan.model_json_schema()["$defs"]["PlannedQuestion"]
    assert source_schema["properties"]["source"]["enum"] == [
        "doctor", "history_followup", "redflag_probe"]
    with pytest.raises(ValidationError):
        QuestionPlan.model_validate_json('{"questions": [{"id": "x", "text": "?", "source": "ai"}]}')


# --- max_urgency ---------------------------------------------------------------------

def test_urgency_order_matches_the_literal():
    assert list(URGENCY_ORDER) == URGENCIES
    ranks = [URGENCY_ORDER[u] for u in URGENCIES]
    assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks)


@pytest.mark.parametrize("low_index", range(len(URGENCIES)))
@pytest.mark.parametrize("high_index", range(len(URGENCIES)))
def test_max_urgency_picks_the_higher_level_in_any_order(low_index, high_index):
    low, high = URGENCIES[low_index], URGENCIES[high_index]
    expected = URGENCIES[max(low_index, high_index)]

    assert max_urgency(low, high) == expected
    assert max_urgency(high, low) == expected


@pytest.mark.parametrize("levels, expected", [
    ((), "none"),
    (("monitor",), "monitor"),
    (("none", "urgent", "monitor"), "urgent"),
    (("urgent", "emergency", "none"), "emergency"),
    (("bogus", "monitor"), "monitor"),  # unknown values rank as "none"
    (("urgent", "bogus"), "urgent"),
])
def test_max_urgency_cases(levels, expected):
    assert max_urgency(*levels) == expected


def test_max_urgency_keeps_a_rule_floor():
    # The primitive behind §6.4 (final = max(rule_based, llm)): an "urgent"
    # rule hit survives an LLM that says "none" or "monitor".
    for llm in ("none", "monitor"):
        assert max_urgency("urgent", llm) == "urgent"
    assert max_urgency("urgent", "emergency") == "emergency"


# --- doctor's agent ------------------------------------------------------------------

@pytest.mark.parametrize("scheduled_at", [None, "2026-10-25T09:00:00+02:00"])
def test_call_job_draft_scheduled_at_is_nullable(scheduled_at):
    draft = CallJobDraft(patient_ref="Maria", situation="post-op day 5",
                         treatment="Lisinopril 10mg", questions=["Any swelling?"],
                         scheduled_at=scheduled_at)

    assert draft.scheduled_at == scheduled_at


def test_call_job_extraction_defaults_are_not_shared():
    first, second = CallJobExtraction(), CallJobExtraction()
    first.candidates.append({"id": 1, "name": "Maria Gomez", "score": 0.9})

    assert second.candidates == []
    assert second.draft is None and second.clarification is None


# --- every `format` schema is a plain JSON object schema -------------------------------

@pytest.mark.parametrize("model", [
    QuestionPlan, PlannedQuestion, CallReportDraft, Soap, CapturedData, TriageResult,
    CallJobDraft,
])
def test_format_schemas_are_json_objects(model):
    schema = model.model_json_schema()

    assert schema["type"] == "object"
    assert schema["properties"]
    assert json.loads(json.dumps(schema)) == schema


# --- context schemas ---------------------------------------------------------------------

def test_patient_context_defaults_and_new_prescription_flag():
    plain = PatientContext(name="John Carter")
    other = PatientContext(name="Someone Else")
    plain.chronic_conditions.append("hypertension")

    assert other.chronic_conditions == [] and other.medications == []
    assert plain.risk_level == "low"
    assert plain.has_new_prescription is False
    maria = PatientContext(name="Maria Gomez", medications=[
        MedicationInfo(name="Metformin"),
        MedicationInfo(name="Lisinopril", dose="10mg", is_new_prescription=True),
    ])
    assert maria.has_new_prescription is True


def test_report_summary_defaults():
    summary = ReportSummary()

    assert summary.summary is None and summary.captured is None
    assert summary.risk_flags == []
