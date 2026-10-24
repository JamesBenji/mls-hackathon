"""Inbound Twilio SMS (ELEVENLABS_TWILIO_SETUP.md step 7).

POST /inbound/sms (application/x-www-form-urlencoded, Twilio Messaging
webhook). Authenticated by X-Twilio-Signature over the PUBLIC URL
(twilio_sms.public_webhook_url), never by X-API-Key; request.url is the
internal URL behind Caddy/ngrok, so it is not used for validation.

Flow for a known patient (matched by E.164 phone):
- "call" / "call me" / "ruf mich an" ...   reply "Calling you now." and start
  the call in a background task (own DB session; the webhook must answer
  Twilio fast, the question plan alone can take ~8 s).
- a time ("18:00", "in 2 hours", "morgen 9 Uhr")   schedule the call, cancel
  a pending callback/retry for the same patient, confirm by SMS.
- anything else   help text.
Any message (except the bare call command) is also scanned for red flags; a
hit creates an urgent sms triage row for the doctor (doctor_callback) and a
doctor notification. Nothing here diagnoses or dispatches emergency
services; the help text points the patient to 112 / 911.

Unknown senders are logged only (no reply). Every valid request gets an empty
TwiML <Response/> with HTTP 200, also when processing fails internally, so
Twilio does not retry a message we already logged.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import database, models, phone, timeutil
from app.adapters import twilio_sms
from app.database import get_db
from app.services import call_orchestrator, notifications, redflags, scheduler, timeparse

log = logging.getLogger(__name__)

router = APIRouter(tags=["inbound"])

EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response/>'
CALL_COMMANDS = frozenset({"call", "call me", "call me now", "anrufen", "ruf an", "ruf mich an"})
CALLING_NOW_TEXT = "Calling you now."
HELP_TEXT = ("Reply CALL to be called now, or a time like 18:00 or 'in 2 hours'. "
             "In an emergency call 112 (EU) or 911 (US).")
EMERGENCY_HINT = " If this is an emergency, call 112 (EU) or 911 (US)."
# Sessions whose pending "resume:<id>" job a new patient request replaces.
_RESUMABLE = ("missed", "callback_pending")


def _twiml() -> Response:
    return Response(content=EMPTY_TWIML, media_type="application/xml")


def _command(body: str) -> str:
    """'  Call me! ' -> 'call me' (case/whitespace/trailing punctuation-insensitive)."""
    return " ".join(body.casefold().split()).strip(" .!?")


@router.post("/inbound/sms")
async def inbound_sms(request: Request, background: BackgroundTasks,
                      db: Session = Depends(get_db)) -> Response:
    form = await request.form()
    signature = request.headers.get("X-Twilio-Signature")
    url = twilio_sms.public_webhook_url(request.url.path, request.url.query)
    if not twilio_sms.validate_twilio_request(url, form, signature):
        log.warning("inbound SMS rejected: invalid or missing X-Twilio-Signature")
        raise HTTPException(status_code=403, detail="invalid Twilio signature")

    from_raw = str(form.get("From") or "")
    body = str(form.get("Body") or "")
    message_sid = str(form.get("MessageSid") or form.get("SmsSid") or "") or None
    sender = phone.normalize_e164(from_raw)

    if message_sid and db.scalars(select(models.SmsLog.id).where(
            models.SmsLog.direction == "inbound",
            models.SmsLog.provider_sid == message_sid)).first() is not None:
        log.info("inbound SMS %s already processed (Twilio retry)", message_sid)
        return _twiml()

    patient = None
    if sender:
        patient = db.scalars(select(models.Patient).where(models.Patient.phone == sender)
                             .order_by(models.Patient.id)).first()
    db.add(models.SmsLog(
        patient_id=patient.id if patient else None, direction="inbound",
        to_number=str(form.get("To") or "")[:32] or None,
        from_number=(sender or from_raw)[:32] or None,
        body=body, kind="inbound", status="received", provider_sid=message_sid,
        sent_at=timeutil.utcnow(),
    ))
    db.commit()

    if patient is None:
        log.info("inbound SMS from unknown number: logged, no reply")
        return _twiml()
    try:
        await _handle_patient_sms(db, background, patient, body)
    except Exception:  # answer Twilio anyway; the message itself is logged
        log.exception("inbound SMS handling failed for patient %s", patient.id)
        db.rollback()
    return _twiml()


async def _handle_patient_sms(db: Session, background: BackgroundTasks,
                              patient: models.Patient, body: str) -> None:
    text = body.strip()
    if _command(text) in CALL_COMMANDS:
        _cancel_pending_resume(db, patient.id)
        await twilio_sms.send_sms(db, patient.phone, CALLING_NOW_TEXT, kind="reply",
                                  patient_id=patient.id)
        background.add_task(start_call_in_background, patient.id)
        return

    hits = _scan(text)
    if hits:
        await _escalate(db, patient, body, hits)

    when = timeparse.parse_requested_time(text, timeutil.now_local())
    if when is not None:
        run_at = timeutil.to_utc_naive(when)
        scheduler.schedule_start_call(patient.id, run_at)
        _cancel_pending_resume(db, patient.id)
        reply = f"Thanks! Guardian will call you {_when_phrase(run_at)}."
        if hits:
            reply += EMERGENCY_HINT
        await twilio_sms.send_sms(db, patient.phone, reply, kind="reply", patient_id=patient.id)
        return

    await twilio_sms.send_sms(db, patient.phone, HELP_TEXT, kind="reply", patient_id=patient.id)


def _when_phrase(run_at_utc) -> str:
    """'at 18:00' today, 'tomorrow at 09:00', else 'on 27.10. at 09:00'."""
    hhmm = timeutil.hhmm_local(run_at_utc)
    days = (timeutil.to_local(run_at_utc).date() - timeutil.now_local().date()).days
    if days <= 0:
        return f"at {hhmm}"
    if days == 1:
        return f"tomorrow at {hhmm}"
    return f"on {timeutil.to_local(run_at_utc):%d.%m.} at {hhmm}"


def _scan(text: str) -> list:
    """Red-flag hits in the SMS. A scanner failure must not block the reply."""
    try:
        return list(redflags.scan_text(text))
    except Exception:
        log.exception("red-flag scan of inbound SMS failed")
        return []


async def _escalate(db: Session, patient: models.Patient, body: str, hits: list) -> None:
    """Urgent sms triage row + doctor notification. The doctor decides; the
    system never dispatches anything itself."""
    now = timeutil.utcnow()
    session = models.CallSession(
        patient_id=patient.id, direction="inbound", channel="sms", status="escalated",
        started_at=now, ended_at=now, caller_number=patient.phone,
    )
    db.add(session)
    db.flush()
    rule_hits = [f"{h.category}: {h.matched}" for h in hits]
    db.add(models.TriageDecision(
        call_session_id=session.id, patient_id=patient.id, urgency="urgent",
        recommended_action="doctor_callback", rationale=f"Patient texted: {body[:300]}",
        source="sms", rule_hits=rule_hits, doctor_reviewed=False,
    ))
    db.commit()
    await notifications.notify_doctor(
        patient_id=patient.id, urgent=True,
        subject=f"URGENT: red-flag SMS from {patient.name}",
        body=f"Patient texted: {body[:300]}\nRule hits: {', '.join(rule_hits)}\n"
             "Recommended: doctor callback (draft, please review).",
    )


def _cancel_pending_resume(db: Session, patient_id: int) -> None:
    """Drop the pending callback/retry of the patient's latest missed or
    callback_pending session so they are not called twice."""
    session = db.scalars(
        select(models.CallSession)
        .where(models.CallSession.patient_id == patient_id,
               models.CallSession.status.in_(_RESUMABLE))
        .order_by(models.CallSession.id.desc())
    ).first()
    if session is None:
        return
    scheduler.cancel(f"resume:{session.id}")
    if session.callback_at is not None:
        # Otherwise scheduler.reload_pending() would re-register it on restart.
        session.callback_at = None
        db.commit()


async def start_call_in_background(patient_id: int) -> None:
    """BackgroundTask: place the SMS-requested call with its own DB session."""
    db = database.SessionLocal()
    try:
        await call_orchestrator.start_call(db, patient_id)
    except Exception:
        log.exception("SMS-requested call for patient %s failed", patient_id)
        db.rollback()
    finally:
        db.close()
