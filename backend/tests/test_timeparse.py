"""Table-driven tests for services/timeparse.parse_requested_time."""
from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.timeparse import parse_requested_time

BERLIN = ZoneInfo("Europe/Berlin")
# Saturday 24 Oct 2026, 14:30 CEST. DST ends the next night (25 Oct 03:00).
NOW = datetime(2026, 10, 24, 14, 30, tzinfo=BERLIN)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=BERLIN)


@pytest.mark.parametrize(("text", "expected"), [
    ("18:00", at(24, 18)),
    ("18.00", at(24, 18)),
    ("6pm", at(24, 18)),
    ("6 PM", at(24, 18)),
    ("6:30 pm", at(24, 18, 30)),
    ("6.30pm", at(24, 18, 30)),
    ("at 18:00", at(24, 18)),
    ("Please call me at 18:30, thanks.", at(24, 18, 30)),
    ("tomorrow 9:00", at(25, 9)),          # crosses the DST change: still 09:00 local
    ("tomorrow at 9am", at(25, 9)),
    ("Tomorrow at 9 a.m.", at(25, 9)),
    ("9:00", at(25, 9)),                   # already past today -> tomorrow
    ("12pm", at(25, 12)),                  # noon already passed
    ("12am", at(25, 0)),
    ("in 2 hours", at(24, 16, 30)),
    ("in 2h", at(24, 16, 30)),
    ("in 30 minutes", at(24, 15)),
    ("in 30 min", at(24, 15)),
    ("in an hour", at(24, 15, 30)),
    ("in half an hour", at(24, 15)),
    ("um 18 Uhr", at(24, 18)),
    ("18 Uhr", at(24, 18)),
    ("um 18:30 Uhr bitte", at(24, 18, 30)),
    ("in 2 Stunden", at(24, 16, 30)),
    ("in einer Stunde", at(24, 15, 30)),
    ("in 30 Minuten", at(24, 15)),
    ("morgen 9 Uhr", at(25, 9)),
    ("Ruf mich morgen um 9 Uhr an", at(25, 9)),
    ("übermorgen 10 Uhr", at(26, 10)),
    ("Guten Morgen, bitte um 18 Uhr", at(24, 18)),  # greeting is not "tomorrow"
])
def test_parses(text: str, expected: datetime) -> None:
    got = parse_requested_time(text, NOW)
    assert got == expected
    assert got.tzinfo is not None
    assert got.utcoffset() == expected.utcoffset()


@pytest.mark.parametrize("text", [
    "",
    "hello",
    "call me",
    "tomorrow",                 # no clock time
    "25:00",
    "18:75",
    "13pm",
    "in 0 minutes",
    "in 200 hours",             # more than 7 days ahead
    "in 3 days",                # unit not supported -> not guessed
    "my sugar was 7.25 mmol",
    "bp 150.90 today",
    "120/80",
    "I took 2 pills",
])
def test_rejects(text: str) -> None:
    assert parse_requested_time(text, NOW) is None


def test_relative_time_is_elapsed_across_dst() -> None:
    late = datetime(2026, 10, 25, 1, 30, tzinfo=BERLIN)  # 01:30 CEST
    got = parse_requested_time("in 2 hours", late)
    # 2 real hours later is 02:30 CET (the 02:00-03:00 hour repeats).
    assert (got.astimezone(UTC) - late.astimezone(UTC)).total_seconds() == 7200
    assert got.utcoffset().total_seconds() == 3600


def test_result_never_in_past_or_beyond_a_week() -> None:
    for text in ("14:30", "14:29", "6am", "in 1 min"):
        got = parse_requested_time(text, NOW)
        assert got is not None and NOW < got <= NOW.replace(day=31)
