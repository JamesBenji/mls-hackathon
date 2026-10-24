"""FastAPI app — Guardian backend (channel-agnostic core + adapters).

Route groups and how they authenticate:
  X-API-Key (GUARDIAN_API_KEY)  patients, vitals, calls, triage, clinical,
                                agent jobs, sms/send, /health/models, reports
  ElevenLabs HMAC signature     POST /calls/webhook
  Bearer ELEVEN_TOOL_AUTH_TOKEN POST /tools/*  (and /inbound/voice-init)
  Twilio X-Twilio-Signature     POST /inbound/sms
  photo-sync token              /photos/* (self-authenticated router)
  none                          GET /health (liveness only, no data)
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app import models  # noqa: F401  (registers tables on Base.metadata)
from app.config import settings
from app.database import Base, engine, get_db
from app.photos.routes import router as photo_router
from app.followups import router as followup_router
from app.photos.tools import router as photo_tools_router
from app.routers import (
    agent_jobs,
    calls,
    clinical,
    eleven_tools,
    eleven_webhooks,
    health,
    inbound_sms,
    patients,
    sms,
    triage,
)
from app.security import require_api_key
from app.services import notify, scheduler

log = logging.getLogger("guardian")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Reconciliation uses additive tables, never alterations to existing columns.
    # `python -m app.seed` resets the schema and loads demo data.
    Base.metadata.create_all(bind=engine)
    if not settings.guardian_api_key:
        log.warning("GUARDIAN_API_KEY is empty: API-key auth is DISABLED (dev only)")
    if settings.mock_telephony or not settings.eleven_configured:
        log.warning("Telephony is in MOCK mode (MOCK_TELEPHONY or missing ElevenLabs creds)")
    if settings.scheduler_enabled:
        scheduler.start()
        scheduler.reload_pending()
    try:
        yield
    finally:
        if settings.scheduler_enabled:
            scheduler.shutdown()


app = FastAPI(title="Guardian API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "Authorization", "Idempotency-Key"],
)

# Webhooks first (so e.g. POST /calls/webhook can never be shadowed by a
# protected /calls/* route). They authenticate by signature / bearer token
# inside the router, NOT by X-API-Key.
app.include_router(eleven_webhooks.router)
app.include_router(eleven_tools.router)
app.include_router(inbound_sms.router)
app.include_router(health.public_router)

# Photo sync authenticates itself (per-device token), so no X-API-Key here.
app.include_router(photo_router)
app.include_router(photo_tools_router)

# Protected by X-API-Key.
_api_key = [Depends(require_api_key)]
app.include_router(patients.router, dependencies=_api_key)
app.include_router(calls.router, dependencies=_api_key)
app.include_router(triage.router, dependencies=_api_key)
app.include_router(clinical.router, dependencies=_api_key)
app.include_router(agent_jobs.router, dependencies=_api_key)
app.include_router(followup_router, dependencies=_api_key)
app.include_router(sms.router, dependencies=_api_key)
app.include_router(health.protected_router, dependencies=_api_key)


# --- Reports: detail, PDF download, doctor notification (dashboard) --------


@app.get("/reports/{report_id}", dependencies=_api_key)
def get_report(report_id: int, db: Session = Depends(get_db)):
    r = db.get(models.CallReport, report_id)
    if not r:
        raise HTTPException(404, "report not found")
    return {
        "id": r.id, "patient_id": r.patient_id,
        "summary": r.summary, "soap_note": r.soap_note,
        "structured_data": r.structured_data, "risk_flags": r.risk_flags,
        "escalated": r.escalated, "pdf_path": r.pdf_path,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@app.get("/reports/{report_id}/pdf", dependencies=_api_key)
async def download_report_pdf(report_id: int, db: Session = Depends(get_db)):
    r = db.get(models.CallReport, report_id)
    if not r:
        raise HTTPException(404, "report not found")
    # Generate on demand if not already rendered.
    if not r.pdf_path:
        await notify.notify_doctor(db, report_id)
        db.refresh(r)
    return FileResponse(r.pdf_path, media_type="application/pdf",
                        filename=f"guardian_report_{report_id}.pdf")


@app.post("/reports/{report_id}/notify", dependencies=_api_key)
async def notify_report(report_id: int, db: Session = Depends(get_db)):
    """Generate PDF + email the doctor (summary + dashboard link + PDF)."""
    try:
        return await notify.notify_doctor(db, report_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
