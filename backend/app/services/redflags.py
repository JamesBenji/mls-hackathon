"""Deterministic red-flag scan, independent of any LLM (MEDGEMMA_DO_SETUP.md §6.4).

Any hit sets a MINIMUM urgency of "urgent" that the LLM cannot lower.
Patterns/thresholds are placeholders to be confirmed by a clinician;
numeric thresholds live in config (settings.redflag_*).

CONTRACT (implemented by the clinical task):

    @dataclass(frozen=True)
    class RedFlagHit:
        category: str     chest_pain | breathing | fainting | stroke | bleeding |
                          suicidal | bp_crisis | hypoglycemia
        matched: str      the matched text / value (for the doctor + rationale)
        source: str       "text" | "vital"

    scan_text(text: str) -> list[RedFlagHit]
        Case-insensitive English + German patterns over the given text.
        Must not fire on clearly negated mentions in the same clause
        ("no chest pain", "keine Brustschmerzen") - keep the negation window
        short so real complaints are never suppressed.
        Also catches spoken/written readings: BP "190/125", "190 over 125",
        "190 zu 125"; glucose "blood sugar 45" / "Zucker 45".

    scan_vitals(captured: CapturedData | dict | None) -> list[RedFlagHit]
        BP systolic > settings.redflag_bp_systolic_above or diastolic >
        settings.redflag_bp_diastolic_above; blood_sugar <
        settings.redflag_glucose_below (mg/dL).

    scan(transcript: str, captured=None) -> list[RedFlagHit]
        scan_text over PATIENT turns only (services.transcripts.patient_text;
        the agent's own probe "any chest pain?" must not trigger) + scan_vitals.
        Deduplicated by (category, matched).

    rule_urgency(hits) -> str      "urgent" if hits else "none"
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RedFlagHit:
    category: str
    matched: str
    source: str  # "text" | "vital"


def scan_text(text: str) -> list[RedFlagHit]:
    raise NotImplementedError


def scan_vitals(captured) -> list[RedFlagHit]:
    raise NotImplementedError


def scan(transcript: str, captured=None) -> list[RedFlagHit]:
    raise NotImplementedError


def rule_urgency(hits: list[RedFlagHit]) -> str:
    raise NotImplementedError
