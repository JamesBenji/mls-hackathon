"""Deterministic red-flag scanner (MEDGEMMA_DO_SETUP.md §6.4, §8).

The scanner is the safety net that runs independently of any model, so the
bias under test is: flag when unsure, but never fire on the agent's own
probe question or on a clearly negated mention.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings
from app.schemas.clinical import CapturedData
from app.services import redflags

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def categories(hits) -> list[str]:
    return [h.category for h in hits]


@pytest.mark.parametrize(("text", "category"), [
    # chest pain
    ("I have chest pain since this morning", "chest_pain"),
    ("My chest hurts when I climb the stairs", "chest_pain"),
    ("There is a tightness in my chest", "chest_pain"),
    ("Chest pressure, mostly at night", "chest_pain"),
    ("Ich habe Brustschmerzen", "chest_pain"),
    ("Ich habe Schmerzen in der Brust", "chest_pain"),
    ("Ich habe ein Engegefühl in der Brust", "chest_pain"),
    ("Engegefuehl in der Brust seit gestern", "chest_pain"),
    # breathing
    ("I can't breathe properly", "breathing"),
    ("I can’t breathe", "breathing"),  # curly apostrophe from speech-to-text
    ("I cannot breathe when I lie down", "breathing"),
    ("I get short of breath on the stairs", "breathing"),
    ("I have shortness of breath", "breathing"),
    ("I have trouble breathing at night", "breathing"),
    ("I'm struggling to breathe", "breathing"),
    ("Ich habe Atemnot", "breathing"),
    ("Ich kriege keine Luft", "breathing"),
    ("Ich bekomme seit gestern schlecht Luft", "breathing"),
    ("Ich kann nicht atmen", "breathing"),
    # fainting
    ("I fainted in the kitchen", "fainting"),
    ("I passed out for a minute", "fainting"),
    ("I blacked out yesterday", "fainting"),
    ("She lost consciousness", "fainting"),
    ("I feel severely dizzy", "fainting"),
    ("Ich war kurz ohnmächtig", "fainting"),
    ("ohnmaechtig geworden", "fainting"),
    ("Ich war bewusstlos", "fainting"),
    ("Ich bin umgekippt", "fainting"),
    # stroke
    ("I think I had a stroke", "stroke"),
    ("My face is drooping on one side", "stroke"),
    ("My wife says I have slurred speech", "stroke"),
    ("I can't move my left arm", "stroke"),
    ("One side of my body is numb", "stroke"),
    ("I feel weak on the right side", "stroke"),
    ("Mein Vater meint, das ist ein Schlaganfall", "stroke"),
    ("Ich habe einen hängenden Mundwinkel", "stroke"),
    ("Ich habe einen haengenden Mundwinkel", "stroke"),
    ("Meine Frau sagt, ich habe eine verwaschene Sprache", "stroke"),
    ("Ich habe eine Lähmung im Arm", "stroke"),
    # bleeding
    ("The wound is bleeding heavily", "bleeding"),
    ("There is severe bleeding from the scar", "bleeding"),
    ("It won't stop bleeding", "bleeding"),
    ("There was a lot of blood on the bandage", "bleeding"),
    ("Es gibt eine starke Blutung", "bleeding"),
    ("Die Wunde blutet stark", "bleeding"),
    # suicidal (never negated: any mention goes to a human)
    ("Sometimes I feel suicidal", "suicidal"),
    ("I want to kill myself", "suicidal"),
    ("I just want to end my life", "suicidal"),
    ("I want to die", "suicidal"),
    ("I don't want to live anymore", "suicidal"),
    ("I would never kill myself", "suicidal"),
    ("Ich denke an Suizid", "suicidal"),
    ("Ich habe an Selbstmord gedacht", "suicidal"),
    ("Ich will mich umbringen", "suicidal"),
    ("Ich will nicht mehr leben", "suicidal"),
    # numeric readings
    ("It was 190/125 this morning", "bp_crisis"),
    ("My pressure was 190 over 125", "bp_crisis"),
    ("Der Blutdruck war 190 zu 125", "bp_crisis"),
    ("It was 185 over 100", "bp_crisis"),
    ("It was 150 over 125", "bp_crisis"),
    ("one ninety over one twenty five", "bp_crisis"),
    ("My blood pressure was 195 this morning", "bp_crisis"),
    ("My blood sugar was 45", "hypoglycemia"),
    ("Zucker 45", "hypoglycemia"),
    ("Mein Blutzucker war heute früh 48 mg/dl", "hypoglycemia"),
    ("Mein Zucker war 2,8 mmol", "hypoglycemia"),
    ("I had a hypo last night", "hypoglycemia"),
    ("Ich hatte eine Unterzuckerung", "hypoglycemia"),
])
def test_positive_phrases(text, category):
    assert category in categories(redflags.scan_text(text))


@pytest.mark.parametrize("text", [
    "No chest pain.",
    "No, no chest pain at all",
    "I don't have any chest pain",
    "I do not have chest pain today",
    "I have not had any chest pain",
    "I never had chest pain",
    "No chest pain or trouble breathing.",
    "I'm not short of breath",
    "I haven't fainted",
    "No heavy bleeding, the wound looks fine",
    "No slurred speech and no facial droop",
    "No severe dizziness",
    "Keine Brustschmerzen",
    "Ich habe keine Schmerzen in der Brust",
    "Nein, keine Atemnot",
    "Ich habe keine Luftnot",
    "Ich habe nie Atemnot gehabt",
    "Ich war nicht bewusstlos",
    "Ohne Brustschmerzen",
    "I feel fine, a little tired",
    "My blood pressure was 140 over 90",
    "Mein Blutdruck war 135 zu 85",
    "My blood sugar was 145",
    "Zucker 6,5",  # mmol/L, normal
    "My sugar was fine, I slept 10 hours",
    "I check my sugar 3 times a day",
    "I took my pills at 8 this morning",
    "The appointment is on 10/24",
    "",
])
def test_negative_phrases(text):
    assert redflags.scan_text(text) == []


@pytest.mark.parametrize(("text", "expected"), [
    # A negated mention never swallows a real complaint in the next clause.
    ("No chest pain, but I can't breathe", ["breathing"]),
    ("Keine Brustschmerzen, aber ich kriege keine Luft", ["breathing"]),
    ("No fever. My chest hurts though.", ["chest_pain"]),
    # A content word between negator and mention keeps the mention.
    ("I don't know why my chest hurts", ["chest_pain"]),
    ("I did not expect this chest pain", ["chest_pain"]),
    # "Never had ... like this / such" is a new complaint, not a denial.
    ("I've never had chest pain like this before", ["chest_pain"]),
    ("I never had such bad chest pain", ["chest_pain"]),
    ("Ich hatte noch nie so starke Brustschmerzen", ["chest_pain"]),
    ("No chest pain, except when I walk", ["chest_pain"]),
    # The negation window is short (3 words).
    ("No, I really don't think that this is chest pain", ["chest_pain"]),
])
def test_negation_is_scoped_to_its_clause(text, expected):
    assert categories(redflags.scan_text(text)) == expected


def test_hit_carries_matched_text_and_source():
    [hit] = redflags.scan_text("Honestly, my CHEST HURTS a lot")
    assert hit == redflags.RedFlagHit("chest_pain", "chest hurts", "text")


def test_repeated_mentions_are_deduplicated():
    hits = redflags.scan_text("Chest pain. Really, chest pain! And my chest hurts.")
    assert [(h.category, h.matched) for h in hits] == [
        ("chest_pain", "chest pain"), ("chest_pain", "chest hurts")]


# --- scan(): patient turns only ------------------------------------------------

def test_agent_probe_alone_never_triggers():
    transcript = (
        "Agent: Are you having any chest pain, trouble breathing, or severe dizziness right now?\n"
        "Patient: No.\n"
        "Agent: Any bleeding from the wound, or did you faint?\n"
        "Patient: No, nothing like that."
    )
    assert redflags.scan(transcript) == []


def test_agent_asked_about_chest_pain_and_patient_denied_it():
    transcript = (
        "Agent: Any chest pain?\n"
        "Patient: No chest pain, I feel fine."
    )
    assert redflags.scan(transcript) == []


def test_chest_pain_fixture_flags_only_what_the_patient_said():
    hits = redflags.scan(fixture("redflag_chest_pain.txt"))
    # The agent's probe also says "trouble breathing" and "severe dizziness";
    # only the patient's own complaint counts.
    assert categories(hits) == ["chest_pain"]
    assert hits[0].source == "text"
    assert redflags.rule_urgency(hits) == "urgent"


def test_chest_pain_fixture_without_the_patient_complaint_is_clean():
    lines = [ln for ln in fixture("redflag_chest_pain.txt").splitlines()
             if not ln.startswith("Patient: Actually yes")]
    assert redflags.scan("\n".join(lines)) == []


def test_german_fixture_flags_breathing_but_not_negated_chest_pain():
    hits = redflags.scan(fixture("redflag_de_atemnot.txt"))
    assert categories(hits) == ["breathing"]


def test_negated_fixture_has_no_hits():
    assert redflags.scan(fixture("no_redflags.txt")) == []


def test_unlabelled_text_is_treated_as_patient_speech():
    assert categories(redflags.scan("my chest hurts")) == ["chest_pain"]


def test_continuation_lines_belong_to_the_patient_turn():
    transcript = "Agent: How are you?\nPatient: Not great.\nI have chest pain since lunch."
    assert categories(redflags.scan(transcript)) == ["chest_pain"]


# --- vitals ----------------------------------------------------------------------

@pytest.mark.parametrize(("captured", "expected"), [
    ({"bp_systolic": 190, "bp_diastolic": 100}, [("bp_crisis", "BP 190/100")]),
    ({"bp_systolic": 150, "bp_diastolic": 125}, [("bp_crisis", "BP 150/125")]),
    ({"bp_systolic": 181}, [("bp_crisis", "BP systolic 181")]),
    ({"bp_systolic": 180, "bp_diastolic": 120}, []),  # thresholds are strict ">"
    ({"blood_sugar": 45}, [("hypoglycemia", "blood sugar 45 mg/dL")]),
    ({"blood_sugar": 54}, []),
    ({"blood_sugar": "40"}, [("hypoglycemia", "blood sugar 40 mg/dL")]),
    ({"bp_systolic": None, "blood_sugar": None}, []),
    ({"bp_systolic": "n/a"}, []),
])
def test_scan_vitals(captured, expected):
    hits = redflags.scan_vitals(captured)
    assert [(h.category, h.matched) for h in hits] == expected
    assert all(h.source == "vital" for h in hits)


def test_scan_vitals_accepts_captured_data_and_none():
    assert categories(redflags.scan_vitals(CapturedData(bp_systolic=200, bp_diastolic=130,
                                                        blood_sugar=40))) == [
        "bp_crisis", "hypoglycemia"]
    assert redflags.scan_vitals(CapturedData()) == []
    assert redflags.scan_vitals(None) == []


def test_thresholds_are_read_from_settings_at_call_time(monkeypatch):
    assert redflags.scan_vitals({"bp_systolic": 150}) == []
    monkeypatch.setattr(settings, "redflag_bp_systolic_above", 140.0)
    monkeypatch.setattr(settings, "redflag_glucose_below", 70.0)
    assert categories(redflags.scan_vitals({"bp_systolic": 150, "blood_sugar": 65})) == [
        "bp_crisis", "hypoglycemia"]
    assert categories(redflags.scan_text("It was 150 over 90")) == ["bp_crisis"]


def test_scan_combines_text_and_vitals_and_dedupes_the_same_reading():
    transcript = "Agent: What was the reading?\nPatient: 190 over 125."
    hits = redflags.scan(transcript, {"bp_systolic": 190, "bp_diastolic": 125})
    assert [(h.category, h.matched, h.source) for h in hits] == [
        ("bp_crisis", "BP 190/125", "text")]


def test_scan_reports_crisis_from_captured_values_alone():
    hits = redflags.scan("Agent: Hello\nPatient: It was high.", CapturedData(bp_systolic=200,
                                                                             bp_diastolic=110))
    assert [(h.category, h.source) for h in hits] == [("bp_crisis", "vital")]


def test_rule_urgency():
    assert redflags.rule_urgency([]) == "none"
    assert redflags.rule_urgency([redflags.RedFlagHit("stroke", "stroke", "text")]) == "urgent"


# --- spoken numbers -----------------------------------------------------------------

@pytest.mark.parametrize(("text", "expected"), [
    ("one forty over ninety", "140 over 90"),
    ("a hundred and forty", "140"),
    ("one hundred forty", "140"),
    ("one oh five", "105"),
    ("one thirty-five over eighty-five", "135 over 85"),
    ("seven and a half hours", "7.5 hours"),
    ("six point five", "6.5"),
    ("two hundred", "200"),
    ("I slept six, seven hours", "I slept 6, 7 hours"),
    ("No one is here", "No one is here"),  # "one" alone is a pronoun
    ("hundertvierzig zu neunzig", "140 zu 90"),
    ("einhundertfünfundzwanzig", "125"),
    ("siebeneinhalb Stunden", "7.5 Stunden"),
    ("Ich trinke ein Glas", "Ich trinke ein Glas"),  # articles stay words
    ("140/90 is already digits", "140/90 is already digits"),
])
def test_normalize_spoken_numbers(text, expected):
    assert redflags.normalize_spoken_numbers(text) == expected


def test_bp_readings():
    assert redflags.bp_readings("It was 140/90, later one thirty over eighty-five") == [
        (140, 90), (130, 85)]
    assert redflags.bp_readings("on 10/24 at 8") == []
