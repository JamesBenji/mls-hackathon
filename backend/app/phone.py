"""Phone number helpers. Everything is stored and compared in E.164."""
from __future__ import annotations

import re

_E164 = re.compile(r"^\+[1-9]\d{6,14}$")


def normalize_e164(raw: str | None) -> str | None:
    """Best-effort E.164 normalization: strip spaces/dashes/parens, turn a
    leading 00 into +. Returns None if the result is not valid E.164.
    Numbers without a country code are rejected rather than guessed."""
    if not raw:
        return None
    s = re.sub(r"[\s\-().]", "", raw.strip())
    if s.startswith("00"):
        s = "+" + s[2:]
    return s if _E164.match(s) else None


def is_e164(value: str | None) -> bool:
    return bool(value) and bool(_E164.match(value))
