"""Agent reasoning — turns patient context + transcript into structured output.

Two jobs (hybrid design from SCOPE.md):
  build_question_plan(): pre-call, history-aware questions (patient check-in agent)
  analyze_call():        post-call triage + report + structured extraction

Both call MedGemma via the Ollama client. JSON is coerced defensively so a
slightly-malformed model response never crashes the API.
"""
from __future__ import annotations

import json
import re

from app import medgemma

TRIAGE_SYSTEM = """You are Guardian, a post-discharge clinical check-in agent.
You are given a patient's context and a phone-call transcript.
Return ONLY a JSON object, no prose, with exactly these keys:
{
  "summary": "<=2 sentence summary for the doctor",
  "soap_note": "S/O/A/P formatted clinical note",
  "structured_data": {"bp_systolic": null, "bp_diastolic": null,
                       "blood_sugar": null, "weight_kg": null,
                       "medication_taken": null, "sleep_hours": null,
                       "side_effects": null},
  "risk_flags": ["..."],
  "urgency": "none|monitor|urgent|emergency",
  "recommended_action": "none|doctor_callback|ambulance|request_appointment",
  "rationale": "why you chose that urgency/action",
  "escalate": true
}
Escalate (urgent/emergency) on: chest pain, difficulty breathing, severe
bleeding, confusion, fainting, suicidal statements, or clear signs the
medication is not working. Fill structured_data only with values actually
stated; use null otherwise."""

PLAN_SYSTEM = """You are Guardian, planning a check-in phone call.
Given the patient's context, the doctor's required questions, and summaries of
previous calls, return ONLY a JSON array of question strings to ask this call.
Start by referencing the last call if one exists (and offer a summary), then the
doctor's required questions, then at most 2 history-aware follow-ups. Keep each
question short and spoken-friendly."""


def _extract_json(text: str) -> dict | list | None:
    """Pull the first JSON object/array out of a model response."""
    match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _patient_context(patient: dict, reports: list[dict]) -> str:
    lines = [
        f"Name: {patient.get('name')}",
        f"Category: {patient.get('category')}  Risk: {patient.get('risk_level')}",
        f"Chronic: {', '.join(patient.get('chronic_conditions') or []) or 'none'}",
    ]
    if patient.get("medications"):
        meds = ", ".join(
            f"{m['name']} {m.get('dose','')}".strip() for m in patient["medications"]
        )
        lines.append(f"Medications: {meds}")
    if reports:
        lines.append("Previous call summaries:")
        for r in reports[:3]:
            lines.append(f"  - {r.get('summary')}")
    return "\n".join(lines)


async def build_question_plan(
    patient: dict, doctor_questions: list[str], reports: list[dict]
) -> list[str]:
    prompt = (
        f"PATIENT CONTEXT:\n{_patient_context(patient, reports)}\n\n"
        f"DOCTOR'S REQUIRED QUESTIONS:\n" + "\n".join(f"- {q}" for q in doctor_questions)
    )
    raw = await medgemma.generate(prompt, system=PLAN_SYSTEM)
    parsed = _extract_json(raw)
    if isinstance(parsed, list) and parsed:
        return [str(q) for q in parsed]
    # Fallback: just use the doctor's questions verbatim.
    return doctor_questions


async def analyze_call(patient: dict, transcript: str, reports: list[dict]) -> dict:
    prompt = (
        f"PATIENT CONTEXT:\n{_patient_context(patient, reports)}\n\n"
        f"CALL TRANSCRIPT:\n{transcript}"
    )
    raw = await medgemma.generate(prompt, system=TRIAGE_SYSTEM)
    parsed = _extract_json(raw)
    if isinstance(parsed, dict):
        parsed.setdefault("urgency", "none")
        parsed.setdefault("recommended_action", "none")
        parsed.setdefault("risk_flags", [])
        parsed.setdefault("escalate", parsed["urgency"] in ("urgent", "emergency"))
        return parsed
    return {
        "summary": "[model output could not be parsed]",
        "raw": raw,
        "urgency": "none",
        "recommended_action": "none",
        "risk_flags": [],
        "escalate": False,
        "structured_data": {},
    }
