"""Time helpers.

Convention: the database stores **naive UTC** datetimes. Anything shown to a
human (SMS "HH:MM", prompts with the current date) is converted to the
clinic timezone (settings.timezone, Europe/Berlin).
"""
from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.config import settings


def utcnow() -> datetime:
    """Current time as naive UTC (the DB convention)."""
    return datetime.now(UTC).replace(tzinfo=None)


def local_tz() -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def now_local() -> datetime:
    """Current time as an aware datetime in the clinic timezone."""
    return datetime.now(local_tz())


def to_local(dt_utc_naive: datetime) -> datetime:
    """Naive-UTC (DB) -> aware local."""
    return dt_utc_naive.replace(tzinfo=UTC).astimezone(local_tz())


def to_utc_naive(dt: datetime) -> datetime:
    """Aware (any tz) -> naive UTC. Naive input is assumed to be local time."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=local_tz())
    return dt.astimezone(UTC).replace(tzinfo=None)


def hhmm_local(dt_utc_naive: datetime) -> str:
    """Format a DB datetime as local 'HH:MM' for SMS / agent speech."""
    return to_local(dt_utc_naive).strftime("%H:%M")
