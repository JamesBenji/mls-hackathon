"""Deterministic parsing of a requested call time from a patient's SMS.

parse_requested_time(text, now) understands short English and German
replies such as "18:00", "18.00", "6:30 pm", "tomorrow at 9am",
"in 2 hours", "in an hour", "um 18 Uhr", "morgen 9 Uhr", "in 30 Minuten".
Pure function, no LLM: it runs inside the Twilio webhook and must be fast
and predictable.

Rules:
- `now` is an aware datetime in the clinic timezone (timeutil.now_local());
  the result is aware in the same timezone, or None if nothing usable was
  found.
- A clock time that has already passed today means tomorrow.
- Impossible clock values (25:00, 18:75, 13pm) and anything more than
  7 days ahead are rejected (None) rather than guessed.
- Numbers followed by a measurement unit ("7.25 mmol", "5.30 kg") are not
  read as times.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

MAX_AHEAD = timedelta(days=7)

_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "ein": 1, "eine": 1, "einer": 1, "einem": 1,
    "two": 2, "zwei": 2, "three": 3, "drei": 3,
}

# "in 2 hours", "in an hour", "in 30 min", "in 2 Stunden", "in einer Stunde"
_RELATIVE = re.compile(
    r"\bin\s+(?P<n>\d{1,4}|an?|one|two|three|einer|einem|eine|ein|zwei|drei)\s*"
    r"(?P<unit>hours?|hrs?|h|minutes?|mins?|stunden?|std|minuten?)(?![a-zäöüß])"
)
_HALF_HOUR = re.compile(r"\bin\s+(?:a\s+)?half\s+an?\s+hour\b|\bin\s+einer\s+halben\s+stunde\b")

# No digit / decimal context around a clock time ("150.90", "7.25.3", "1/2").
_BEFORE = r"(?<![\d/])(?<!\d[.,:])"
# ...and no measurement unit right after it ("7.25 mmol", "5.30 kg").
_NOT_UNIT = r"(?!\s*(?:mg|mmol|kg|lb|mmhg|bpm|cm|%|°))"

_AMPM = re.compile(
    _BEFORE + r"(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))?\s*(?P<ap>[ap])\.?\s?m(?![a-z])"
)
_UHR = re.compile(_BEFORE + r"(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))?\s*uhr\b")
_CLOCK = re.compile(_BEFORE + r"(?P<h>\d{1,2})[:.](?P<m>\d{2})(?![\d:/]|[.,]\d)" + _NOT_UNIT)

_DAY_AFTER_TOMORROW = re.compile(r"\b(?:day\s+after\s+tomorrow|übermorgen)\b")
_TOMORROW = re.compile(r"\b(?:tomorrow|tmrw|morgen)\b")
_GREETING = re.compile(r"\bguten\s+morgen\b")  # "good morning", not "tomorrow"


def parse_requested_time(text: str, now: datetime) -> datetime | None:
    """Requested call time in `text`, as an aware datetime in now's timezone."""
    s = " ".join((text or "").casefold().split())
    if not s:
        return None

    delta = _relative_delta(s)
    if delta is not None:
        if delta <= timedelta(0) or delta > MAX_AHEAD:
            return None
        return _add_elapsed(now, delta)

    clock = _clock_time(s)
    if clock is None:
        return None
    hour, minute = clock

    s_days = _GREETING.sub(" ", s)
    offset = 2 if _DAY_AFTER_TOMORROW.search(s_days) else 1 if _TOMORROW.search(s_days) else 0
    # Wall-clock arithmetic on purpose: "tomorrow 9:00" stays 09:00 across DST.
    when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if offset:
        when += timedelta(days=offset)
    elif when <= now:
        when += timedelta(days=1)  # already past today -> tomorrow
    if not (now < when <= now + MAX_AHEAD):
        return None
    return when


def _relative_delta(s: str) -> timedelta | None:
    if _HALF_HOUR.search(s):
        return timedelta(minutes=30)
    m = _RELATIVE.search(s)
    if not m:
        return None
    raw = m.group("n")
    amount = int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
    unit = m.group("unit")
    if unit.startswith(("h", "st")):
        return timedelta(hours=amount)
    return timedelta(minutes=amount)


def _clock_time(s: str) -> tuple[int, int] | None:
    """(hour, minute) of the first clock time found, or None if absent/invalid."""
    m = _AMPM.search(s)
    if m:
        hour, minute = int(m.group("h")), int(m.group("m") or 0)
        if not (1 <= hour <= 12 and 0 <= minute <= 59):
            return None
        hour %= 12
        if m.group("ap") == "p":
            hour += 12
        return hour, minute
    m = _UHR.search(s) or _CLOCK.search(s)
    if m:
        hour, minute = int(m.group("h")), int(m.group("m") or 0)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        return hour, minute
    return None


def _add_elapsed(now: datetime, delta: timedelta) -> datetime:
    """now + delta in real elapsed time (correct across DST changes)."""
    if now.tzinfo is None:
        return now + delta
    return (now.astimezone(UTC) + delta).astimezone(now.tzinfo)
