"""Health routes.

public_router:    GET /health          liveness only {"status": "ok"}; no data,
                                       no auth (load balancers, uptime checks)
protected_router: GET /health/models   per-model status via
                                       ollama_client.probe_models() plus the
                                       telephony / scheduler mode. X-API-Key
                                       is applied in main.py.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from app.adapters import elevenlabs, ollama_client, twilio_sms
from app.config import settings
from app.services import scheduler

log = logging.getLogger(__name__)

public_router = APIRouter(tags=["health"])
protected_router = APIRouter(tags=["health"])


@public_router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@protected_router.get("/health/models")
async def health_models() -> dict:
    report = dict(await ollama_client.probe_models())
    report["telephony_mock"] = _telephony_mock()
    report["sms_mock"] = _sms_mock()
    report["scheduler_running"] = _scheduler_running()
    return report


def _telephony_mock() -> bool:
    try:
        return bool(elevenlabs.is_mock())
    except Exception:  # adapter not available: report the configured intent
        log.warning("elevenlabs.is_mock() failed; deriving mock mode from settings",
                    exc_info=True)
        return settings.mock_telephony or not settings.eleven_configured


def _sms_mock() -> bool:
    try:
        return bool(twilio_sms.is_mock())
    except Exception:
        log.warning("twilio_sms.is_mock() failed; deriving mock mode from settings",
                    exc_info=True)
        return settings.mock_telephony or not settings.twilio_configured


def _scheduler_running() -> bool:
    try:
        return bool(scheduler.get_scheduler().running)
    except Exception:
        log.warning("could not read scheduler state", exc_info=True)
        return False
