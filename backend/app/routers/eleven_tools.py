"""ElevenLabs agent webhook tools (Bearer ELEVEN_TOOL_AUTH_TOKEN).

POST /tools/schedule_callback  -> {"ok": true, "callback_time": "HH:MM"}
POST /tools/flag_emergency     -> {"ok": true}

Called mid-call, so handlers stay fast (DB writes + at most one SMS).
Unknown session -> 404 {"ok": false, "error": "unknown session"}.
"""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models, timeutil
from app.database import get_db
from app.security import require_tool_token
from app.services import call_orchestrator

log = logging.getLogger(__name__)

router = APIRouter(prefix="/tools", tags=["agent-tools"],
                   dependencies=[Depends(require_tool_token)])


class ScheduleCallbackIn(BaseModel):
    session_id: int | str
    minutes: int | float | str | None = None
    missing_item: str | None = None
    checkpoint: str | None = None


class FlagEmergencyIn(BaseModel):
    session_id: int | str
    reason: str = ""
    recommended_action: Literal["doctor_callback", "ambulance", "request_appointment"] = (
        "doctor_callback")


def _session(db: Session, session_id: int | str) -> models.CallSession | None:
    try:
        sid = int(str(session_id).strip())
    except (TypeError, ValueError):
        return None
    return db.get(models.CallSession, sid)


def _unknown_session() -> JSONResponse:
    return JSONResponse(status_code=404, content={"ok": False, "error": "unknown session"})


@router.post("/schedule_callback")
async def schedule_callback(body: ScheduleCallbackIn, db: Session = Depends(get_db)):
    session = _session(db, body.session_id)
    if session is None:
        return _unknown_session()
    try:
        callback_at = await call_orchestrator.schedule_callback(
            db, session, minutes=body.minutes, missing_item=body.missing_item,
            checkpoint=body.checkpoint)
    except ValueError as exc:
        return JSONResponse(status_code=409, content={"ok": False, "error": str(exc)})
    return {"ok": True, "callback_time": timeutil.hhmm_local(callback_at)}


@router.post("/flag_emergency")
async def flag_emergency(body: FlagEmergencyIn, db: Session = Depends(get_db)):
    session = _session(db, body.session_id)
    if session is None:
        return _unknown_session()
    await call_orchestrator.flag_emergency(db, session, reason=body.reason,
                                           recommended_action=body.recommended_action)
    return {"ok": True}
