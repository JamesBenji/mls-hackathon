"""Transcript rendering + role helpers shared by webhooks, red-flag scan and
the clinical service.

Canonical text form (what we store in transcriptions.text and feed to the
models) is one turn per line:

    Agent: Did you take your blood pressure today?
    Patient: No, not yet.

ElevenLabs roles map as agent -> "Agent", user -> "Patient".
"""
from __future__ import annotations

import re

_ROLE_LINE = re.compile(r"^\s*(agent|patient|user|assistant|ai|guardian)\s*:\s*(.*)$", re.I)
_PATIENT_ROLES = {"patient", "user"}
# Filler that does not count as a real patient response (voicemail detection).
_FILLER = re.compile(r"^[\W_]*(?:h+m+|u+h+|u+m+|ah+|oh+|\.\.\.)?[\W_]*$", re.I)


def render_transcript(turns: list[dict] | None) -> str:
    """ElevenLabs `data.transcript` turns -> canonical text."""
    lines: list[str] = []
    for t in turns or []:
        msg = (t.get("message") or "").strip()
        if not msg:
            continue
        role = "Patient" if (t.get("role") or "").lower() == "user" else "Agent"
        lines.append(f"{role}: {msg}")
    return "\n".join(lines)


def has_role_labels(text: str) -> bool:
    return any(_ROLE_LINE.match(line) for line in text.splitlines())


def patient_turns(text: str) -> list[str]:
    """Patient utterances only. If the text has no role labels at all (e.g. a
    manually pasted note), the whole text is treated as patient speech."""
    if not has_role_labels(text):
        return [text] if text.strip() else []
    out: list[str] = []
    current_is_patient = False
    for line in text.splitlines():
        m = _ROLE_LINE.match(line)
        if m:
            current_is_patient = m.group(1).lower() in _PATIENT_ROLES
            if current_is_patient and m.group(2).strip():
                out.append(m.group(2).strip())
        elif current_is_patient and line.strip() and out:
            out[-1] = f"{out[-1]} {line.strip()}"  # continuation line
    return out


def patient_text(text: str) -> str:
    return "\n".join(patient_turns(text))


def has_patient_response(text: str) -> bool:
    """False for voicemail / silence: no patient turn with real words."""
    if not has_role_labels(text):
        return bool(text.strip())
    return any(not _FILLER.match(t) and len(t.split()) >= 1 for t in patient_turns(text))
