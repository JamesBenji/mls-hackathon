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
"""
from __future__ import annotations

from datetime import datetime


def get_scheduler():
    raise NotImplementedError


def reset_scheduler() -> None:
    raise NotImplementedError


def start() -> None:
    raise NotImplementedError


def shutdown() -> None:
    raise NotImplementedError


def schedule_resume(session_id: int, run_at_utc: datetime) -> str:
    raise NotImplementedError


def schedule_start_call(patient_id: int, run_at_utc: datetime, *,
                        call_job_id: int | None = None) -> str:
    raise NotImplementedError


def cancel(job_id: str) -> None:
    raise NotImplementedError


def get_job(job_id: str):
    raise NotImplementedError


def reload_pending() -> int:
    raise NotImplementedError


async def run_resume_call(session_id: int) -> None:
    raise NotImplementedError


async def run_start_call(patient_id: int, call_job_id: int | None = None) -> None:
    raise NotImplementedError
