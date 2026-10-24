"""APScheduler (3.x) for callbacks, retries and doctor-scheduled calls.

CONTRACT (implemented by the context+scheduler task):

    get_scheduler() -> AsyncIOScheduler
        Lazy singleton. If settings.scheduler_enabled: SQLAlchemyJobStore on
        settings.scheduler_jobstore_url or settings.database_url (jobs survive
        restarts). Otherwise a MemoryJobStore scheduler that is never started
        (tests inspect pending jobs via get_job()).
    reset_scheduler() -> None               tests: drop the singleton
    start() / shutdown()                    called from main.py lifespan
    schedule_resume(session_id: int, run_at_utc: datetime) -> str
        job id f"resume:{session_id}", replace_existing=True, generous
        misfire_grace_time + coalesce. run_at is naive UTC (DB convention).
    schedule_start_call(patient_id: int, run_at_utc: datetime, *,
                        call_job_id: int | None = None) -> str
        job id f"start:{patient_id}" or f"job:{call_job_id}".
    cancel(job_id: str) -> None             no error if missing
    get_job(job_id: str)                    -> apscheduler Job | None
    reload_pending() -> int
        On startup: re-register every call_sessions row with
        status == "callback_pending" and callback_at set (past-due ones run
        ~immediately). Returns how many were registered. Opens its own DB
        session.

    Job entrypoints (module-level, referenced textually so the SQLAlchemy
    job store can persist them; each opens its own SessionLocal):
    async run_resume_call(session_id: int) -> None
        -> call_orchestrator.resume_call(db, session_id)
    async run_start_call(patient_id: int, call_job_id: int | None = None) -> None
        -> call_orchestrator.start_call(db, patient_id, call_job_id=call_job_id)

Implementation notes:
- Jobs use a date trigger with an aware-UTC run_date (the scheduler runs in
  UTC); callers pass naive UTC like everything else in the DB. Pending jobs
  of a stopped scheduler only expose job.trigger.run_date (next_run_time is
  filled in on start); job_run_at_utc() reads whichever is available.
- While the scheduler is stopped, APScheduler queues add_job() calls without
  honouring replace_existing, so schedule_* drops our own pending copy first:
  exactly one job per id in both states.
- shutdown() completes synchronously on the scheduler's own event loop
  (FastAPI lifespan) or once that loop has closed (tests); upstream
  AsyncIOScheduler only queues the stop via call_soon_threadsafe.
- Job bodies log and swallow exceptions: a failing call must never take the
  scheduler down. The orchestrator owns retries/attempt caps.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import UTC, datetime, timedelta

from apscheduler.job import Job
from apscheduler.jobstores.base import JobLookupError
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.base import STATE_STOPPED, BaseScheduler
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import database, models, timeutil
from app.config import settings

log = logging.getLogger(__name__)

# Textual refs ("module:function") so the SQLAlchemy job store can pickle jobs.
RESUME_FUNC = "app.services.scheduler:run_resume_call"
START_FUNC = "app.services.scheduler:run_start_call"

MISFIRE_GRACE_S = 3600  # a job up to 1 h late (e.g. server restart) still runs
JOB_DEFAULTS = {"coalesce": True, "max_instances": 1, "misfire_grace_time": MISFIRE_GRACE_S}
# reload_pending(): past-due callbacks run this many seconds after startup.
RELOAD_MIN_DELAY_S = 5


class _GuardianScheduler(AsyncIOScheduler):
    """AsyncIOScheduler with a deterministic shutdown.

    Upstream (3.11) shutdown() only queues the real stop on the event loop:
    `running` stays True until the loop gets to it, never happens if that
    loop has stopped, and raises if it is closed. On the scheduler's own loop
    thread (lifespan), or when its loop is not running at all, nothing can
    race us, so stop inline; only a loop running in another thread gets the
    upstream thread-safe hand-off.
    """

    def shutdown(self, wait: bool = True) -> None:
        loop = self._eventloop
        if self.running and (loop is None or loop.is_closed() or not loop.is_running()
                             or _is_current_loop(loop)):
            BaseScheduler.shutdown(self, wait)
            self._stop_timer()
            self._eventloop = None
            return
        super().shutdown(wait)

    def _process_jobs(self):
        # A wakeup queued before an inline shutdown may still run after it.
        if self.state == STATE_STOPPED:
            return None
        return super()._process_jobs()


def _is_current_loop(loop: asyncio.AbstractEventLoop) -> bool:
    try:
        return asyncio.get_running_loop() is loop
    except RuntimeError:
        return False


_scheduler: AsyncIOScheduler | None = None
_lock = threading.Lock()


def _build_scheduler() -> AsyncIOScheduler:
    if settings.scheduler_enabled:
        url = settings.scheduler_jobstore_url or settings.database_url
        # Own engine (never the app's: the store disposes it on shutdown);
        # make_engine adds pool_pre_ping / SQLite thread settings.
        store = SQLAlchemyJobStore(engine=database.make_engine(url))
        log.info("scheduler: persistent job store (%s)", store.engine.dialect.name)
    else:
        store = MemoryJobStore()
        log.info("scheduler disabled (SCHEDULER_ENABLED=false): jobs are only recorded")
    return _GuardianScheduler(jobstores={"default": store}, timezone=UTC,
                              job_defaults=dict(JOB_DEFAULTS))


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        with _lock:
            if _scheduler is None:
                _scheduler = _build_scheduler()
    return _scheduler


def _stop(sched: AsyncIOScheduler, wait: bool) -> None:
    try:
        sched.shutdown(wait=wait)
    except Exception:  # best effort, e.g. a job store failed to close its pool
        log.warning("error while shutting the scheduler down", exc_info=True)


def reset_scheduler() -> None:
    global _scheduler
    with _lock:
        sched, _scheduler = _scheduler, None
    if sched is not None and sched.running:
        _stop(sched, wait=False)


def start() -> None:
    """Start processing jobs. Call inside the running event loop (lifespan).
    Idempotent; a no-op when SCHEDULER_ENABLED is false."""
    if not settings.scheduler_enabled:
        log.info("scheduler disabled; not starting")
        return
    sched = get_scheduler()
    if sched.running:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError as exc:
        raise RuntimeError("scheduler.start() must run inside the event loop "
                           "(FastAPI lifespan)") from exc
    sched.start()
    log.info("scheduler started")


def shutdown(wait: bool = False) -> None:
    """Stop the scheduler (lifespan). Idempotent; persisted jobs stay stored."""
    sched = _scheduler
    if sched is None or not sched.running:
        return
    _stop(sched, wait)
    log.info("scheduler stopped")


# --- scheduling --------------------------------------------------------------

def _aware_utc(dt: datetime) -> datetime:
    """Naive UTC (DB convention) -> aware UTC; aware input is converted."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _naive_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is None else dt.astimezone(UTC).replace(tzinfo=None)


def _add_date_job(job_id: str, func_ref: str, args: list, run_at_utc: datetime) -> str:
    sched = get_scheduler()
    run_date = _aware_utc(run_at_utc)
    if not sched.running:
        # Stopped schedulers queue add_job() blindly; keep one job per id.
        cancel(job_id)
    sched.add_job(
        func_ref, trigger=DateTrigger(run_date=run_date, timezone=UTC), args=args,
        id=job_id, name=job_id, replace_existing=True,
        misfire_grace_time=MISFIRE_GRACE_S, coalesce=True, max_instances=1,
    )
    log.info("scheduled %s at %s UTC", job_id, run_date.isoformat(timespec="seconds"))
    return job_id


def schedule_resume(session_id: int, run_at_utc: datetime) -> str:
    return _add_date_job(f"resume:{session_id}", RESUME_FUNC, [session_id], run_at_utc)


def schedule_start_call(patient_id: int, run_at_utc: datetime, *,
                        call_job_id: int | None = None) -> str:
    job_id = f"job:{call_job_id}" if call_job_id is not None else f"start:{patient_id}"
    return _add_date_job(job_id, START_FUNC, [patient_id, call_job_id], run_at_utc)


def cancel(job_id: str) -> None:
    try:
        get_scheduler().remove_job(job_id)
    except JobLookupError:
        pass


def get_job(job_id: str) -> Job | None:
    return get_scheduler().get_job(job_id)


def job_run_at_utc(job_id: str) -> datetime | None:
    """When the job will run, as naive UTC (works for pending jobs too)."""
    job = get_job(job_id)
    if job is None:
        return None
    when = getattr(job, "next_run_time", None)  # unset while pending
    if when is None:
        when = getattr(job.trigger, "run_date", None)
    return _naive_utc(when) if when is not None else None


def reload_pending() -> int:
    """Re-register callback_pending sessions (startup). Never raises."""
    count = 0
    db = None
    try:
        db = database.SessionLocal()
        from app.followups import reload_plans
        reload_plans(db)
        rows = db.execute(
            select(models.CallSession.id, models.CallSession.callback_at)
            .where(models.CallSession.status == "callback_pending",
                   models.CallSession.callback_at.is_not(None))
            .order_by(models.CallSession.callback_at)
        ).all()
        earliest = timeutil.utcnow() + timedelta(seconds=RELOAD_MIN_DELAY_S)
        for session_id, callback_at in rows:
            try:
                schedule_resume(session_id, max(_naive_utc(callback_at), earliest))
                count += 1
            except Exception:
                log.exception("could not re-register the callback for session %s", session_id)
        log.info("reload_pending: %d callback(s) registered", count)
    except Exception:
        log.exception("reload_pending failed; pending callbacks were not re-registered")
    finally:
        if db is not None:
            db.close()
    return count


# --- job entrypoints ---------------------------------------------------------

async def run_resume_call(session_id: int) -> None:
    db = None
    try:
        # Lazy import: the orchestrator imports this module.
        from app.services import call_orchestrator

        db = database.SessionLocal()
        log.info("scheduled resume for session %s", session_id)
        await call_orchestrator.resume_call(db, session_id)
        db.commit()
    except Exception:
        log.exception("scheduled resume_call failed for session %s", session_id)
        _rollback(db)
    finally:
        if db is not None:
            db.close()


async def run_start_call(patient_id: int, call_job_id: int | None = None) -> None:
    db = None
    try:
        from app.services import call_orchestrator

        db = database.SessionLocal()
        if call_job_id is not None:
            # Old extracted jobs have no recorded explicit approval. Only the
            # authorized legacy /start endpoint may execute them, never a queue.
            log.warning("Skipped legacy queued call job %s; explicit review/start required", call_job_id)
            return
        log.info("scheduled call for patient %s (call job %s)", patient_id, call_job_id)
        await call_orchestrator.start_call(db, patient_id, call_job_id=call_job_id)
        db.commit()
    except Exception:
        log.exception("scheduled start_call failed for patient %s (call job %s)",
                      patient_id, call_job_id)
        _rollback(db)
    finally:
        if db is not None:
            db.close()


def _rollback(db: Session | None) -> None:
    if db is None:
        return
    try:
        db.rollback()
    except Exception:
        log.debug("rollback after a failed job raised", exc_info=True)
