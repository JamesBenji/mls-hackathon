"""ElevenLabs webhooks (signature auth, NOT X-API-Key).

POST /calls/webhook        post-call events (HMAC `elevenlabs-signature`).
                           Verified events always get 200 {"status":
                           "received"}; background failures never change it.
                           If the synchronous persistence itself fails we
                           return 500 so ElevenLabs retries (handling is
                           idempotent on conversation_id).
POST /inbound/voice-init   conversation-initiation webhook for inbound Twilio
                           calls. Auth: Bearer ELEVEN_TOOL_AUTH_TOKEN or a
                           valid elevenlabs signature. Must be fast.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import security
from app.adapters import elevenlabs
from app.database import get_db
from app.services import call_orchestrator, context_builder

log = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


@router.post("/calls/webhook")
async def eleven_post_call_webhook(request: Request, background: BackgroundTasks,
                                   db: Session = Depends(get_db)) -> dict:
    raw = await request.body()
    try:
        event = elevenlabs.verify_webhook(raw, request.headers.get("elevenlabs-signature"))
    except elevenlabs.WebhookVerificationError as exc:
        log.warning("rejected ElevenLabs webhook: %s", exc)
        raise HTTPException(status_code=401, detail="invalid webhook signature") from None
    try:
        return await call_orchestrator.process_webhook_event(db, event, background)
    except Exception:
        log.exception("ElevenLabs webhook processing failed (type=%s)",
                      event.get("type") if isinstance(event, dict) else None)
        db.rollback()
        raise HTTPException(status_code=500, detail="webhook processing failed") from None


def _voice_init_authorized(request: Request, raw: bytes) -> bool:
    if security.bearer_token_ok(request.headers.get("authorization")):
        return True
    signature = request.headers.get("elevenlabs-signature")
    if not signature:
        return False
    try:
        elevenlabs.verify_webhook(raw, signature)
        return True
    except Exception as exc:
        log.warning("voice-init signature rejected: %s", exc)
        return False


@router.post("/inbound/voice-init")
async def inbound_voice_init(request: Request, db: Session = Depends(get_db)) -> dict:
    raw = await request.body()
    if not _voice_init_authorized(request, raw):
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid JSON") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="expected a JSON object")
    try:
        return await call_orchestrator.handle_inbound_voice_init(db, payload)
    except Exception:
        # Never block call pickup: answer with safe defaults instead.
        log.exception("voice-init failed; answering with unknown-caller defaults")
        db.rollback()
        return {"type": "conversation_initiation_client_data",
                "dynamic_variables": context_builder.unknown_caller_variables(0)}
