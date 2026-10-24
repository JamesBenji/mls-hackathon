"""scheduler: job registration, startup reload and the job bodies
(ELEVENLABS_TWILIO_SETUP.md Step 5).

Tests run with SCHEDULER_ENABLED=false, i.e. a MemoryJobStore scheduler that
is never started (jobs stay *pending*). The enabled path is covered against a
real SQLAlchemyJobStore on a temporary SQLite file. call_orchestrator is
implemented in parallel, so its functions are monkeypatched.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from sqlalchemy import create_engine, make_url, text

from app import database, models
from app.config import settings
from app.services import call_orchestrator, scheduler
from app.timeutil import utcnow


class SpySession:
    """Stand-in for database.SessionLocal() that records the lifecycle."""

    def __init__(self) -> None:
        self.committed = self.rolled_back = self.closed = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def spy_db(monkeypatch):
    sessions: list[SpySession] = []

    def factory():
        sessions.append(SpySession())
        return sessions[-1]

    monkeypatch.setattr(database, "SessionLocal", factory)
    return sessions


@pytest.fixture
def enabled(tmp_path, monkeypatch):
    """Persistent scheduler on a temp SQLite file. Returns the job store URL."""
    url = f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}"
    monkeypatch.setattr(settings, "scheduler_enabled", True)
    monkeypatch.setattr(settings, "scheduler_jobstore_url", url)
    scheduler.reset_scheduler()
    yield url
    scheduler.shutdown()
    scheduler.reset_scheduler()


def _stored_job_ids(url: str) -> list[str]:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return [row[0] for row in conn.execute(text("SELECT id FROM apscheduler_jobs"))]
    finally:
        engine.dispose()


# --- singleton / configuration -----------------------------------------------

def test_disabled_scheduler_is_memory_backed_and_never_started():
    sched = scheduler.get_scheduler()
    assert scheduler.get_scheduler() is sched
    assert isinstance(sched._jobstores["default"], MemoryJobStore)
    assert sched.timezone.utcoffset(None) == timedelta(0)
    assert sched._job_defaults == {"coalesce": True, "max_instances": 1,
                                   "misfire_grace_time": 3600}

    scheduler.start()  # SCHEDULER_ENABLED=false -> no-op
    assert not sched.running
    scheduler.shutdown()  # not running -> no-op, no error
    scheduler.shutdown()

    scheduler.reset_scheduler()
    assert scheduler.get_scheduler() is not sched


def test_enabled_scheduler_uses_sqlalchemy_store(enabled):
    store = scheduler.get_scheduler()._jobstores["default"]
    assert isinstance(store, SQLAlchemyJobStore)
    assert store.engine.url == make_url(enabled)


def test_jobstore_falls_back_to_database_url(monkeypatch, tmp_path):
    url = f"sqlite:///{(tmp_path / 'app.db').as_posix()}"
    monkeypatch.setattr(settings, "scheduler_enabled", True)
    monkeypatch.setattr(settings, "scheduler_jobstore_url", "")
    monkeypatch.setattr(settings, "database_url", url)
    scheduler.reset_scheduler()
    assert scheduler.get_scheduler()._jobstores["default"].engine.url == make_url(url)


def test_start_outside_event_loop_fails_clearly(enabled):
    with pytest.raises(RuntimeError, match="inside the event loop"):
        scheduler.start()
    assert not scheduler.get_scheduler().running


# --- scheduling (disabled scheduler: pending jobs) ---------------------------

def test_schedule_resume_registers_pending_job():
    run_at = utcnow() + timedelta(minutes=15)

    assert scheduler.schedule_resume(7, run_at) == "resume:7"

    job = scheduler.get_job("resume:7")
    assert job is not None and job.pending
    assert job.func_ref == "app.services.scheduler:run_resume_call"
    assert job.func is scheduler.run_resume_call
    assert job.args == (7,) and job.kwargs == {}
    run_date = job.trigger.run_date
    assert run_date.tzinfo is not None and run_date.utcoffset() == timedelta(0)
    assert run_date == run_at.replace(tzinfo=UTC)
    assert run_date.replace(tzinfo=None) == run_at
    assert (job.misfire_grace_time, job.coalesce, job.max_instances) == (3600, True, 1)
    assert scheduler.job_run_at_utc("resume:7") == run_at


def test_schedule_start_call_ids_and_args():
    run_at = utcnow() + timedelta(hours=2)

    assert scheduler.schedule_start_call(3, run_at) == "start:3"
    assert scheduler.schedule_start_call(3, run_at, call_job_id=9) == "job:9"

    plain, from_job = scheduler.get_job("start:3"), scheduler.get_job("job:9")
    assert plain.args == (3, None) and from_job.args == (3, 9)
    for job in (plain, from_job):
        assert job.func_ref == "app.services.scheduler:run_start_call"
        assert job.trigger.run_date == run_at.replace(tzinfo=UTC)


def test_rescheduling_replaces_the_existing_job():
    first, second = utcnow() + timedelta(minutes=15), utcnow() + timedelta(minutes=1)
    scheduler.schedule_resume(7, first)
    scheduler.schedule_resume(7, second)
    scheduler.schedule_start_call(3, first)
    scheduler.schedule_start_call(3, second)

    ids = [j.id for j in scheduler.get_scheduler().get_jobs()]
    assert sorted(ids) == ["resume:7", "start:3"]
    assert scheduler.get_job("resume:7").trigger.run_date == second.replace(tzinfo=UTC)
    assert scheduler.job_run_at_utc("start:3") == second


def test_aware_run_time_is_converted_to_utc():
    berlin = datetime(2026, 10, 24, 14, 0, tzinfo=ZoneInfo("Europe/Berlin"))  # CEST
    scheduler.schedule_resume(8, berlin)
    assert scheduler.get_job("resume:8").trigger.run_date == datetime(2026, 10, 24, 12, 0,
                                                                      tzinfo=UTC)
    assert scheduler.job_run_at_utc("resume:8") == datetime(2026, 10, 24, 12, 0)


def test_cancel_removes_job_and_ignores_unknown_ids():
    scheduler.schedule_resume(7, utcnow() + timedelta(minutes=5))
    scheduler.cancel("resume:7")
    assert scheduler.get_job("resume:7") is None
    scheduler.cancel("resume:7")  # already gone
    scheduler.cancel("resume:does-not-exist")
    assert scheduler.get_job("nope") is None
    assert scheduler.job_run_at_utc("nope") is None


# --- reload_pending ----------------------------------------------------------

def test_reload_pending_registers_only_callback_pending_sessions(db, seeded):
    now = utcnow()

    def session(status, callback_at):
        return models.CallSession(patient_id=seeded["maria_id"], direction="outbound",
                                  channel="voice", status=status, callback_at=callback_at)

    future = session("callback_pending", now + timedelta(minutes=10))
    overdue = session("callback_pending", now - timedelta(minutes=30))
    no_time = session("callback_pending", None)
    done = session("completed", now + timedelta(minutes=10))
    missed = session("missed", now - timedelta(minutes=5))
    db.add_all([future, overdue, no_time, done, missed])
    db.commit()

    before = utcnow()
    assert scheduler.reload_pending() == 2
    after = utcnow()

    assert scheduler.job_run_at_utc(f"resume:{future.id}") == future.callback_at
    overdue_at = scheduler.job_run_at_utc(f"resume:{overdue.id}")
    assert before + timedelta(seconds=5) <= overdue_at <= after + timedelta(seconds=5)
    assert scheduler.get_job(f"resume:{overdue.id}").args == (overdue.id,)
    for other in (no_time, done, missed):
        assert scheduler.get_job(f"resume:{other.id}") is None
    assert scheduler.get_job(f"resume:{seeded['prior_session_id']}") is None

    # Running it again (e.g. second worker start) keeps one job per session.
    assert scheduler.reload_pending() == 2
    assert len(scheduler.get_scheduler().get_jobs()) == 2


def test_reload_pending_with_nothing_to_do(db, seeded):
    assert scheduler.reload_pending() == 0
    assert scheduler.get_scheduler().get_jobs() == []


def test_reload_pending_never_raises(monkeypatch, caplog):
    def broken_session():
        raise RuntimeError("database is down")

    monkeypatch.setattr(database, "SessionLocal", broken_session)
    with caplog.at_level(logging.ERROR, logger="app.services.scheduler"):
        assert scheduler.reload_pending() == 0
    assert "reload_pending failed" in caplog.text


def test_reload_pending_skips_a_failing_session(db, seeded, monkeypatch):
    now = utcnow()
    rows = [models.CallSession(patient_id=seeded["maria_id"], direction="outbound",
                               status="callback_pending", callback_at=now + timedelta(minutes=m))
            for m in (5, 10)]
    db.add_all(rows)
    db.commit()
    real = scheduler.schedule_resume

    def flaky(session_id, run_at):
        if session_id == rows[0].id:
            raise ValueError("bad row")
        return real(session_id, run_at)

    monkeypatch.setattr(scheduler, "schedule_resume", flaky)
    assert scheduler.reload_pending() == 1
    assert scheduler.get_job(f"resume:{rows[1].id}") is not None


# --- job bodies --------------------------------------------------------------

async def test_run_resume_call_calls_orchestrator(monkeypatch, spy_db):
    calls = []

    async def fake_resume(db, session_id):
        calls.append((db, session_id))

    monkeypatch.setattr(call_orchestrator, "resume_call", fake_resume, raising=False)

    await scheduler.run_resume_call(5)

    [db] = spy_db
    assert calls == [(db, 5)]
    assert db.committed and db.closed and not db.rolled_back


async def test_run_resume_call_swallows_exceptions(monkeypatch, spy_db, caplog):
    async def boom(db, session_id):
        raise RuntimeError("ElevenLabs is down")

    monkeypatch.setattr(call_orchestrator, "resume_call", boom, raising=False)

    with caplog.at_level(logging.ERROR, logger="app.services.scheduler"):
        await scheduler.run_resume_call(5)  # must not raise

    [db] = spy_db
    assert db.rolled_back and db.closed and not db.committed
    assert "resume_call failed for session 5" in caplog.text


async def test_run_start_call_calls_orchestrator(monkeypatch, spy_db):
    calls = []

    async def fake_start(db, patient_id, *, call_job_id=None):
        calls.append((db, patient_id, call_job_id))
        return {"call_session_id": 1}

    monkeypatch.setattr(call_orchestrator, "start_call", fake_start)

    await scheduler.run_start_call(3, 9)
    await scheduler.run_start_call(4)

    assert [(p, j) for _, p, j in calls] == [(3, 9), (4, None)]
    assert [c[0] for c in calls] == spy_db
    assert all(s.closed and s.committed for s in spy_db)


async def test_run_start_call_swallows_exceptions(monkeypatch, spy_db):
    async def boom(db, patient_id, *, call_job_id=None):
        raise LookupError("patient 3 not found")

    monkeypatch.setattr(call_orchestrator, "start_call", boom)
    await scheduler.run_start_call(3)
    assert spy_db[0].rolled_back and spy_db[0].closed


# --- enabled scheduler (persistent store) ------------------------------------

async def test_enabled_scheduler_persists_jobs_across_restarts(enabled):
    sched = scheduler.get_scheduler()
    scheduler.start()
    scheduler.start()  # idempotent
    assert sched.running

    run_at = utcnow() + timedelta(hours=1)
    assert scheduler.schedule_resume(42, run_at) == "resume:42"
    scheduler.schedule_resume(42, run_at)  # replace_existing in the store
    job = scheduler.get_job("resume:42")
    assert not job.pending
    assert job.next_run_time == run_at.replace(tzinfo=UTC)
    assert _stored_job_ids(enabled) == ["resume:42"]

    scheduler.shutdown()
    assert not sched.running  # synchronous, even inside the loop
    scheduler.shutdown()      # idempotent
    await asyncio.sleep(0.05)  # queued wakeups must be harmless after shutdown
    assert _stored_job_ids(enabled) == ["resume:42"]  # still persisted

    # "Restart the server": a fresh scheduler on the same store sees the job.
    scheduler.reset_scheduler()
    restarted = scheduler.get_scheduler()
    assert restarted is not sched
    scheduler.start()
    job = scheduler.get_job("resume:42")
    assert job is not None
    assert job.func_ref == "app.services.scheduler:run_resume_call"
    assert job.args == (42,)
    assert job.next_run_time == run_at.replace(tzinfo=UTC)
    assert scheduler.job_run_at_utc("resume:42") == run_at

    scheduler.cancel("resume:42")
    assert scheduler.get_job("resume:42") is None
    assert _stored_job_ids(enabled) == []
    scheduler.shutdown()
    assert not restarted.running


async def test_enabled_scheduler_runs_a_due_job(enabled, monkeypatch):
    ran = asyncio.Event()
    seen = []

    async def fake_resume(db, session_id):
        seen.append(session_id)
        ran.set()

    monkeypatch.setattr(call_orchestrator, "resume_call", fake_resume, raising=False)
    scheduler.start()

    scheduler.schedule_resume(5, utcnow() - timedelta(seconds=30))  # overdue, within grace
    await asyncio.wait_for(ran.wait(), timeout=5)

    assert seen == [5]
    assert scheduler.get_job("resume:5") is None  # one-shot job is gone
    assert _stored_job_ids(enabled) == []


async def test_reset_scheduler_stops_a_running_scheduler(enabled):
    scheduler.start()
    sched = scheduler.get_scheduler()
    scheduler.reset_scheduler()
    assert not sched.running


def test_lifespan_starts_reloads_and_stops_scheduler(enabled, db, seeded):
    from fastapi.testclient import TestClient

    from app.main import app

    pending = models.CallSession(patient_id=seeded["maria_id"], direction="outbound",
                                 channel="voice", status="callback_pending",
                                 callback_at=utcnow() + timedelta(minutes=30))
    db.add(pending)
    db.commit()

    with TestClient(app):
        sched = scheduler.get_scheduler()
        assert sched.running
        assert scheduler.get_job(f"resume:{pending.id}") is not None
        assert _stored_job_ids(enabled) == [f"resume:{pending.id}"]
    assert not sched.running
