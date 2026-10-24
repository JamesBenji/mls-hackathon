"""Call orchestration: the channel-agnostic core of a check-in call.
Talks to the outside world only through app/adapters.

CONTRACT (implemented by the orchestration task; other tasks call the
functions marked [shared]):

    class CallStartError(Exception)       placing the call failed (router -> 502)

    [shared] async start_call(db, patient_id: int, *, call_job_id: int | None = None) -> dict
        1. call_sessions row (scheduled, outbound, voice), flush for id
        2. context_builder.build_dynamic_variables (plan has 8 s fallback)
        3. elevenlabs.place_outbound_call -> store conversation_id, call_sid,
           attempt=1, call_attempts row, status=in_progress
        Returns {"call_session_id": int, "status": str,
                 "question_plan": list[str], "conversation_id": str | None,
                 "call_sid": str | None, "mock": bool}
        Raises LookupError (unknown patient) or CallStartError (session is
        marked failed with last_error first).

    async resume_call(db, session_id: int) -> dict | None
        Scheduler job body. attempt >= settings.max_call_attempts -> SMS the
        patient, flag the doctor (triage row source="system"), status=failed,
        return None. Else re-place the call with resume_from=<checkpoint text>
        and the same context, attempt += 1.

    async schedule_callback(db, session, *, minutes: int | None,
                            missing_item: str | None, checkpoint: str | None) -> datetime
        status=callback_pending, callback_at=now+minutes (default
        settings.callback_default_minutes), checkpoint JSON, scheduler job,
        SMS "Guardian will call you back at HH:MM to record your <item>."
        Returns callback_at (naive UTC).

    async flag_emergency(db, session, *, reason: str, recommended_action: str)
        -> models.TriageDecision

    async process_webhook_event(db, event: dict, background) -> dict
        post_call_transcription / call_initiation_failure / post_call_audio,
        idempotent on conversation_id. Returns {"status": "received", ...}.

    async handle_post_call(session_id: int) -> None
        Background task with its own DB session: voicemail check ->
        generate_report -> triage -> vitals write-back -> risk proposal ->
        notify. Each step isolated; failures mark the report status=partial
        with the error stored; the transcript is never lost.

    async handle_inbound_voice_init(db, payload: dict) -> dict
        ElevenLabs conversation-initiation webhook for inbound Twilio calls.

Implementation notes:
- A session can span several conversations (initial call + "call me back"
  resumes, see models.CallAttempt). The report is generated ONCE over all
  transcripts of the session, after the final conversation; a transcript
  that arrives while a callback is pending is stored and deferred.
- Everything clinical is a DRAFT for the doctor (doctor_reviewed=False).
  The proposed risk level is stored on the report, never applied to the
  patient. The system never dispatches emergency services itself.
- Cross-module calls go through module attributes (elevenlabs.x,
  clinical.x, ...) so tests can monkeypatch them.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import database, models, timeutil
from app.adapters import elevenlabs, twilio_sms
from app.config import settings
from app.phone import normalize_e164
from app.schemas.clinical import PlannedQuestion, QuestionPlan
from app.services import clinical, context_builder, notifications, redflags, scheduler, transcripts

log = logging.getLogger(__name__)

TOOL_ACTIONS = ("doctor_callback", "ambulance", "request_appointment")
INBOUND_OPENING = "Ask how you can help today and listen to their concern."
_MAX_ERROR_CHARS = 500
_SNIPPET_CHARS = 600

# CapturedData field -> (vitals.type, unit)
_VITAL_FIELDS: dict[str, tuple[str, str]] = {
    "bp_systolic": ("bp_systolic", "mmHg"),
    "bp_diastolic": ("bp_diastolic", "mmHg"),
    "blood_sugar": ("blood_sugar", "mg/dL"),
    "weight_kg": ("weight", "kg"),
    "sleep_hours": ("sleep_hours", "h"),
}


class CallStartError(Exception):
    """Placing the outbound call failed."""


# --- small helpers -----------------------------------------------------------

def _err(exc: BaseException) -> str:
    # Provider exception bodies can echo prompts, transcripts and runtime credentials.
    return type(exc).__name__


def _patient(db: Session, session: models.CallSession) -> models.Patient | None:
    return db.get(models.Patient, session.patient_id) if session.patient_id else None


def _schedule_resume(session_id: int, run_at: datetime) -> None:
    try:
        scheduler.schedule_resume(session_id, run_at)
    except Exception:  # the DB state is the source of truth (reload_pending)
        log.exception("could not schedule the resume job for session %s", session_id)


def _cancel_resume(session_id: int) -> None:
    try:
        scheduler.cancel(f"resume:{session_id}")
    except Exception:
        log.debug("cancel resume:%s failed", session_id, exc_info=True)


async def _sms(db: Session, patient: models.Patient | None, body: str, *, kind: str) -> None:
    if patient is None or not patient.phone:
        return
    try:
        await twilio_sms.send_sms(db, patient.phone, body, kind=kind, patient_id=patient.id)
    except Exception:  # send_sms should never raise; be defensive anyway
        log.exception("SMS (%s) to patient %s failed", kind, patient.id)


async def _notify(*, patient_id: int | None, subject: str, body: str, urgent: bool) -> None:
    try:
        await notifications.notify_doctor(patient_id=patient_id, subject=subject, body=body,
                                          urgent=urgent)
    except Exception:
        log.warning("notify_doctor failed (patient_id=%s)", patient_id)


def _fallback_plan(questions: list[str]) -> QuestionPlan:
    try:
        return clinical.fallback_plan(questions)
    except Exception:
        log.exception("clinical.fallback_plan failed; using a local static plan")
        return QuestionPlan(questions=[
            PlannedQuestion(id=f"q{i}", text=q, source="doctor")
            for i, q in enumerate(questions, 1)
        ])


def render_plan(plan: QuestionPlan) -> str:
    """clinical.render_plan_for_agent, or a plain numbered list if that fails."""
    try:
        text = clinical.render_plan_for_agent(plan)
        if isinstance(text, str) and text.strip():
            return text
    except Exception:
        log.exception("clinical.render_plan_for_agent failed; using a numbered list")
    return "\n".join(f"{i}. {q.text}" for i, q in enumerate(plan.questions, 1))


async def question_plan_for(db: Session, patient_id: int) -> QuestionPlan:
    """History-aware plan for a patient (never raises except LookupError)."""
    ctx = context_builder.load_patient_context(db, patient_id)
    reports = context_builder.load_last_reports(db, patient_id)
    questions = context_builder.doctor_questions_for(db, patient_id)
    try:
        plan = await clinical.build_question_plan(ctx, reports, questions)
        if isinstance(plan, QuestionPlan) and plan.questions:
            return plan
    except Exception as exc:
        log.warning("build_question_plan failed for patient %s (%s); using fallback",
                    patient_id, type(exc).__name__)
    return _fallback_plan(questions)


# --- outbound calls ------------------------------------------------------------

async def start_call(db: Session, patient_id: int, *, call_job_id: int | None = None,
                     follow_up_plan_id: int | None = None,
                     one_off_approval: dict | None = None) -> dict:
    patient = db.get(models.Patient, patient_id)
    if patient is None:
        raise LookupError(f"patient {patient_id} not found")
    if call_job_id is not None:
        job = db.get(models.CallJob, call_job_id)
        if job is None or job.patient_id != patient_id or job.doctor_id != patient.doctor_id:
            raise LookupError("Call job not found for this patient/doctor")

    session = models.CallSession(patient_id=patient.id, direction="outbound", channel="voice",
                                 status="scheduled", call_job_id=call_job_id, attempt=0)
    db.add(session)
    db.flush()
    if follow_up_plan_id is not None:
        from app.followups import FollowUpPlan, PlanCall
        import secrets
        approved = db.scalar(select(FollowUpPlan).where(FollowUpPlan.id == follow_up_plan_id)
                             .execution_options(populate_existing=True).with_for_update())
        allowed_statuses = ("active", "paused", "draft") if one_off_approval else ("active",)
        if (approved is None or approved.status not in allowed_statuses or approved.patient_id != patient_id
                or approved.doctor_id != patient.doctor_id):
            db.rollback()
            raise CallStartError("Plan is not active for this patient")
        if one_off_approval and approved.revision != one_off_approval["revision"]:
            db.rollback()
            raise CallStartError("Plan changed; review it again before calling")
        snapshot = {key: getattr(approved, key) for key in (
            "instructions", "context", "questions", "tracked_items", "photo_policy", "revision")}
        if one_off_approval:
            snapshot["one_off_approval"] = one_off_approval
        prior = db.scalar(select(PlanCall.session_id).where(PlanCall.plan_id == approved.id))
        db.add(PlanCall(session_id=session.id, plan_id=approved.id,
                       photo_request_key=secrets.token_hex(24),
                       photo_allowed=approved.photo_policy == "every_call" or (
                           approved.photo_policy == "first_call" and prior is None),
                        approved_context=snapshot))
        db.flush()
    try:
        variables, plan = await context_builder.build_dynamic_variables(
            db, session, resume_from="start")
        if one_off_approval:
            result = await elevenlabs.place_outbound_call(
                patient.phone, variables,
                max_duration_seconds=one_off_approval["max_duration_seconds"],
                branch_id=settings.eleven_immediate_call_branch_id,
            )
        else:
            result = await elevenlabs.place_outbound_call(patient.phone, variables)
    except Exception as exc:
        session.status = "dispatch_unknown" if one_off_approval else "failed"
        session.last_error = type(exc).__name__
        db.commit()
        log.error("call start failed for patient %s (session %s): %s",
                  patient.id, session.id, session.last_error)
        raise CallStartError(session.last_error) from exc

    mock = bool(result.get("mock"))
    session.conversation_id = result.get("conversation_id")
    session.call_sid = result.get("call_sid")
    session.attempt = 1
    session.started_at = timeutil.utcnow()
    session.status = "in_progress"
    session.last_error = None
    db.add(models.CallAttempt(
        call_session_id=session.id, attempt=1, conversation_id=session.conversation_id,
        call_sid=session.call_sid, resume_from="start", status="mock" if mock else "placed"))
    if call_job_id is not None:
        job = db.get(models.CallJob, call_job_id)
        if job is not None:
            job.status = "done"
    db.commit()
    log.info("call placed for patient %s: session %s conversation %s (mock=%s)",
             patient.id, session.id, session.conversation_id, mock)
    return {
        "call_session_id": session.id,
        "status": session.status,
        "question_plan": [q.text for q in plan.questions],
        "conversation_id": session.conversation_id,
        "call_sid": session.call_sid,
        "mock": mock,
    }


async def resume_call(db: Session, session_id: int) -> dict | None:
    session = db.get(models.CallSession, session_id)
    if session is None:
        log.warning("resume_call: session %s not found", session_id)
        return None
    if session.status not in ("callback_pending", "missed"):
        log.info("resume_call: session %s is %s; nothing to resume", session_id, session.status)
        return None
    from app.followups import FollowUpPlan, PlanCall
    linked = db.get(PlanCall, session.id)
    if linked and (linked.approved_context.get("one_off_approval")
                   or db.get(FollowUpPlan, linked.plan_id).status != "active"):
        session.status, session.callback_at = "cancelled", None
        db.commit()
        return None
    if session.attempt >= settings.max_call_attempts:
        await give_up(db, session)
        return None
    patient = _patient(db, session)
    if patient is None:
        log.warning("resume_call: session %s has no patient; cannot call back", session_id)
        return None

    resume_from = str((session.checkpoint or {}).get("text") or "").strip() or "start"
    try:
        variables, _plan = await context_builder.build_dynamic_variables(
            db, session, resume_from=resume_from)
        result = await elevenlabs.place_outbound_call(patient.phone, variables)
    except Exception as exc:
        session.attempt += 1
        session.last_error = _err(exc)
        db.add(models.CallAttempt(call_session_id=session.id, attempt=session.attempt,
                                  resume_from=resume_from, status="failed",
                                  failure_reason=session.last_error,
                                  ended_at=timeutil.utcnow()))
        log.error("resume call for session %s failed (attempt %s): %s",
                  session.id, session.attempt, session.last_error)
        if session.attempt >= settings.max_call_attempts:
            db.commit()
            await give_up(db, session)
            return None
        next_at = timeutil.utcnow() + timedelta(minutes=settings.missed_call_retry_minutes)
        session.callback_at = next_at
        db.commit()
        _schedule_resume(session.id, next_at)
        return None

    mock = bool(result.get("mock"))
    session.attempt += 1
    session.conversation_id = result.get("conversation_id")
    session.call_sid = result.get("call_sid")
    session.status = "in_progress"
    session.callback_at = None
    session.last_error = None
    if session.started_at is None:
        session.started_at = timeutil.utcnow()
    db.add(models.CallAttempt(
        call_session_id=session.id, attempt=session.attempt,
        conversation_id=session.conversation_id, call_sid=session.call_sid,
        resume_from=resume_from, status="mock" if mock else "placed"))
    db.commit()
    log.info("resumed session %s (attempt %s, conversation %s)",
             session.id, session.attempt, session.conversation_id)
    return {
        "call_session_id": session.id,
        "attempt": session.attempt,
        "conversation_id": session.conversation_id,
        "call_sid": session.call_sid,
        "resume_from": resume_from,
        "mock": mock,
    }


def _coerce_minutes(minutes: Any) -> int:
    value: float | None = None
    if isinstance(minutes, bool):
        value = None
    elif isinstance(minutes, (int, float)):
        value = float(minutes)
    elif isinstance(minutes, str):
        m = re.search(r"\d+(?:[.,]\d+)?", minutes)
        if m:
            value = float(m.group(0).replace(",", "."))
    if value is None or value != value:  # None or NaN
        value = float(settings.callback_default_minutes)
    return int(min(max(round(value), 1), 1440))


async def schedule_callback(db: Session, session: models.CallSession, *,
                            minutes: int | float | str | None = None,
                            missing_item: str | None = None,
                            checkpoint: str | None = None) -> datetime:
    if session.patient_id is None:
        raise ValueError("cannot call back an unknown caller")
    from app.followups import FollowUpPlan, PlanCall
    linked = db.get(PlanCall, session.id)
    if linked and linked.approved_context.get("one_off_approval"):
        raise ValueError("This call was approved for one attempt only; callbacks are disabled")
    if linked and db.get(FollowUpPlan, linked.plan_id).status != "active":
        raise ValueError("Follow-up plan is not active")
    mins = _coerce_minutes(minutes)
    callback_at = timeutil.utcnow() + timedelta(minutes=mins)
    item = (missing_item or "").strip() or None
    session.status = "callback_pending"
    session.callback_at = callback_at
    session.checkpoint = {
        "text": (checkpoint or "").strip(),
        "missing_item": item,
        "conversation_id": session.conversation_id,
    }
    db.commit()
    _schedule_resume(session.id, callback_at)
    await _sms(db, _patient(db, session),
               f"Guardian will call you back at {timeutil.hhmm_local(callback_at)} "
               f"to record your {item or 'reading'}.", kind="callback")
    log.info("callback for session %s in %s min (%s)", session.id, mins, item)
    return callback_at


async def flag_emergency(db: Session, session: models.CallSession, *, reason: str,
                         recommended_action: str) -> models.TriageDecision:
    action = recommended_action if recommended_action in TOOL_ACTIONS else "doctor_callback"
    urgency = "emergency" if action == "ambulance" else "urgent"
    reason = (reason or "").strip() or "no reason given"
    row = models.TriageDecision(
        call_session_id=session.id, patient_id=session.patient_id, urgency=urgency,
        recommended_action=action, source="agent_tool", doctor_reviewed=False,
        rationale=f"Flagged by the voice agent during the call: {reason}")
    db.add(row)
    session.status = "escalated"
    db.commit()
    patient = _patient(db, session)
    who = patient.name if patient else f"unknown caller {session.caller_number or ''}".strip()
    await _notify(patient_id=session.patient_id, urgent=True,
                  subject=f"URGENT: {who} flagged during a Guardian call",
                  body=(f"The voice agent flagged {who} ({urgency}). Recommended action: "
                        f"{action}. Reason: {reason}. Please review now (call session "
                        f"{session.id}). Guardian does not contact emergency services itself."))
    return row


async def give_up(db: Session, session: models.CallSession, background: Any = None) -> None:
    """Attempt cap reached: stop calling, tell the patient + the doctor."""
    attempts = session.attempt
    session.status = "failed"
    session.callback_at = None
    _cancel_resume(session.id)
    db.add(models.TriageDecision(
        call_session_id=session.id, patient_id=session.patient_id, urgency="monitor",
        recommended_action="doctor_callback", source="system", doctor_reviewed=False,
        rationale=f"Patient unreachable after {attempts} attempts"))
    db.commit()
    patient = _patient(db, session)
    await _sms(db, patient, "We could not reach you for your Guardian check-in. "
                            "Your doctor's office will contact you.", kind="missed")
    name = patient.name if patient else "unknown patient"
    await _notify(patient_id=session.patient_id, urgent=False,
                  subject=f"Patient unreachable: {name}",
                  body=f"Guardian could not reach {name} after {attempts} attempts "
                       f"(call session {session.id}). Please contact the patient.")
    # A partial interview (e.g. before a callback that never connected) is
    # still reported. Only if there is real patient speech, so the voicemail
    # path of handle_post_call can never loop back here.
    if transcripts.has_patient_response(_combined_text(db, session.id)):
        if background is not None:
            background.add_task(handle_post_call, session.id)
        else:
            await handle_post_call(session.id)


async def _handle_missed(db: Session, session: models.CallSession, reason: str,
                         background: Any = None) -> dict:
    """Missed call / voicemail: SMS + retry later, or give up at the cap."""
    from app.followups import FollowUpPlan, PlanCall
    linked = db.get(PlanCall, session.id)
    if linked and linked.approved_context.get("one_off_approval"):
        session.status = "missed"
        session.last_error = reason[:_MAX_ERROR_CHARS] if reason else None
        session.callback_at = None
        _cancel_resume(session.id)
        db.commit()
        return {"missed": True, "retry_scheduled": False}
    if linked and db.get(FollowUpPlan, linked.plan_id).status != "active":
        session.status, session.callback_at = "cancelled", None
        _cancel_resume(session.id)
        db.commit()
        return {"cancelled": True}
    session.status = "missed"
    session.last_error = reason[:_MAX_ERROR_CHARS] if reason else None
    if session.attempt >= settings.max_call_attempts:
        db.commit()
        await give_up(db, session, background)
        return {"gave_up": True}
    next_at = timeutil.utcnow() + timedelta(minutes=settings.missed_call_retry_minutes)
    session.callback_at = next_at
    db.commit()
    _schedule_resume(session.id, next_at)
    await _sms(db, _patient(db, session),
               "We tried to reach you for your check-in. Guardian will call again at "
               f"{timeutil.hhmm_local(next_at)}. Reply CALL to be called now.", kind="missed")
    return {"retry_at": next_at.isoformat()}


# --- ElevenLabs webhooks ---------------------------------------------------------

def _session_id_from_vars(data: dict) -> int | None:
    init = data.get("conversation_initiation_client_data") or {}
    dyn = init.get("dynamic_variables") if isinstance(init, dict) else None
    raw = dyn.get("session_id") if isinstance(dyn, dict) else None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _find_session(db: Session, data: dict) -> tuple[models.CallSession | None,
                                                    models.CallAttempt | None]:
    conv_id = data.get("conversation_id")
    attempt = None
    if conv_id:
        attempt = db.scalars(select(models.CallAttempt)
                             .where(models.CallAttempt.conversation_id == conv_id)).first()
    session = None
    sid = _session_id_from_vars(data)
    if sid is not None:
        session = db.get(models.CallSession, sid)
    if session is None and attempt is not None:
        session = db.get(models.CallSession, attempt.call_session_id)
    if session is None and conv_id:
        session = db.scalars(select(models.CallSession)
                             .where(models.CallSession.conversation_id == conv_id)
                             .order_by(models.CallSession.id.desc())).first()
    if attempt is not None and session is not None and attempt.call_session_id != session.id:
        attempt = None
    if attempt is None and session is not None:
        # Inbound calls learn their conversation id only now.
        attempt = db.scalars(select(models.CallAttempt)
                             .where(models.CallAttempt.call_session_id == session.id,
                                    models.CallAttempt.conversation_id.is_(None),
                                    models.CallAttempt.status.in_(("placed", "mock")))
                             .order_by(models.CallAttempt.attempt.desc())).first()
        if attempt is not None and conv_id:
            attempt.conversation_id = conv_id
    return session, attempt


async def process_webhook_event(db: Session, event: dict, background: Any) -> dict:
    etype = event.get("type") if isinstance(event, dict) else None
    data = event.get("data") if isinstance(event, dict) else None
    if not isinstance(data, dict):
        data = {}
    if etype == "post_call_transcription":
        return await _on_transcription(db, data, background)
    if etype == "call_initiation_failure":
        return await _on_initiation_failure(db, data, background)
    log.info("ElevenLabs webhook %s for conversation %s ignored", etype,
             data.get("conversation_id"))
    return {"status": "received", "ignored": True}


async def _on_transcription(db: Session, data: dict, background: Any) -> dict:
    conv_id = data.get("conversation_id") or None
    duplicate = {"status": "received", "duplicate": True}
    if conv_id and db.scalars(select(models.Transcription.id)
                              .where(models.Transcription.conversation_id == conv_id)).first():
        log.info("duplicate post_call_transcription for %s ignored", conv_id)
        return duplicate

    session, attempt = _find_session(db, data)
    turns = data.get("transcript") if isinstance(data.get("transcript"), list) else []
    analysis = data.get("analysis") if isinstance(data.get("analysis"), dict) else None
    db.add(models.Transcription(
        source="call", text=transcripts.render_transcript(turns), turns=turns,
        analysis=analysis, conversation_id=conv_id,
        call_session_id=session.id if session else None,
        patient_id=session.patient_id if session else None))

    deferred = False
    if session is None:
        log.warning("post_call_transcription %s matches no call session; stored unlinked",
                    conv_id)
    else:
        now = timeutil.utcnow()
        if attempt is not None:
            attempt.status = "completed"
            attempt.ended_at = now
        if session.conversation_id is None:
            session.conversation_id = conv_id
        # Defer while a callback is pending, or when this is an older
        # conversation and a newer one (the resume) is still running.
        deferred = session.status == "callback_pending" or (
            session.status == "in_progress" and bool(conv_id)
            and session.conversation_id not in (None, conv_id))
        if not deferred:
            if session.status != "escalated":
                session.status = "completed"
            session.ended_at = now
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        log.info("duplicate post_call_transcription for %s (race) ignored", conv_id)
        return duplicate

    if session is None:
        return {"status": "received", "call_session_id": None}
    if deferred:
        log.info("session %s: transcript stored, report deferred (callback pending)", session.id)
        return {"status": "received", "call_session_id": session.id, "deferred": True}
    background.add_task(handle_post_call, session.id)
    return {"status": "received", "call_session_id": session.id}


async def _on_initiation_failure(db: Session, data: dict, background: Any) -> dict:
    session, attempt = _find_session(db, data)
    reason = str(data.get("failure_reason") or "call not connected")
    if session is None:
        log.warning("call_initiation_failure for %s matches no session (%s)",
                    data.get("conversation_id"), reason)
        return {"status": "received", "call_session_id": None}
    if attempt is not None and attempt.status in ("missed", "failed"):
        return {"status": "received", "duplicate": True, "call_session_id": session.id}
    if attempt is None and session.status in ("missed", "failed"):
        return {"status": "received", "duplicate": True, "call_session_id": session.id}
    if attempt is not None:
        attempt.status = "missed"
        attempt.failure_reason = reason[:_MAX_ERROR_CHARS]
        attempt.ended_at = timeutil.utcnow()
    log.info("session %s: call not connected (%s)", session.id, reason)
    info = await _handle_missed(db, session, f"call not connected: {reason}", background)
    return {"status": "received", "call_session_id": session.id, "missed": True, **info}


# --- post-call pipeline ----------------------------------------------------------

def _combined_text(db: Session, session_id: int) -> str:
    rows = db.scalars(select(models.Transcription)
                      .where(models.Transcription.call_session_id == session_id)
                      .order_by(models.Transcription.created_at.asc(),
                                models.Transcription.id.asc())).all()
    return "\n".join(r.text.strip() for r in rows if r.text and r.text.strip())


def _history(db: Session, patient_id: int, exclude_session_id: int | None) -> list:
    """Last N reports as agent memory, excluding this session's own report."""
    own: set[int] = set()
    if exclude_session_id is not None:
        own = set(db.scalars(select(models.CallReport.id)
                             .where(models.CallReport.call_session_id == exclude_session_id)))
    n = settings.history_reports_n
    reports = context_builder.load_last_reports(db, patient_id, n + len(own))
    return [r for r in reports if r.id not in own][:n]


def _soap_note(soap: Any) -> str | None:
    if soap is None:
        return None
    return (f"S: {soap.subjective}\nO: {soap.objective}\n"
            f"A: {soap.assessment}\nP: {soap.plan}")


def _triage_dict(outcome: Any) -> dict:
    res = outcome.result
    return {
        "urgency": res.urgency,
        "recommended_action": res.recommended_action,
        "rationale": res.rationale,
        "uncertain_because": list(res.uncertain_because or []),
        "rule_hits": list(outcome.rule_hits or []),
        "llm_urgency": outcome.llm_urgency,
    }


def _fallback_triage(text: str, captured: Any, reason: str) -> dict:
    """Triage itself crashed: rules if possible, else ask the doctor to review."""
    hits: list[str] = []
    try:
        hits = [f"{h.category}: {h.matched}" for h in redflags.scan(text, captured)]
    except Exception:
        log.debug("red-flag scan unavailable in fallback triage", exc_info=True)
    return {
        "urgency": "urgent" if hits else "monitor",
        "recommended_action": "doctor_callback",
        "rationale": f"Automated triage failed ({reason}); please review the transcript.",
        "uncertain_because": ["automated triage unavailable"],
        "rule_hits": hits,
        "llm_urgency": None,
    }


async def analyze_transcript(db: Session, patient_id: int, text: str, *,
                             call_session_id: int | None = None, source: str = "call",
                             notify: bool = True) -> dict:
    """Shared report + triage pipeline (post-call job and /clinical/analyze).

    Persists only when `call_session_id` is given. Each step is isolated:
    a failure marks the report "partial" with the error stored, and the
    report is committed before triage so the work is never lost.

    Returns {"report": draft dict | None, "report_error": str | None,
             "triage": dict | None, "saved": {"report_id", "triage_id"} | None,
             "errors": list[str]}.
    Raises LookupError for an unknown patient.
    """
    ctx = context_builder.load_patient_context(db, patient_id)
    reports = _history(db, patient_id, call_session_id)
    errors: list[str] = []

    # (1) report draft
    gen, report_error = None, None
    try:
        gen = await clinical.generate_report_detailed(text, ctx, reports)
    except Exception as exc:
        report_error = _err(exc)
        errors.append(f"report generation failed: {report_error}")
        log.warning("report generation failed for patient %s: %s", patient_id, report_error)
    draft = gen.draft if gen is not None else None

    # (2) persist the report BEFORE triage
    report = None
    if call_session_id is not None:
        report = models.CallReport(call_session_id=call_session_id, patient_id=patient_id,
                                   status="partial" if draft is None else "ok")
        if draft is None:
            report.error = errors[0]
            report.summary = f"[report generation failed: {report_error}] Transcript saved."
        else:
            report.summary = draft.summary
            report.soap = draft.soap.model_dump()
            report.soap_note = _soap_note(draft.soap)
            report.structured_data = draft.captured.model_dump()
            report.risk_flags = list(draft.risk_flags or [])
            report.changes_since_last_call = list(draft.changes_since_last_call or [])
            report.clinical_analysis = gen.clinical_analysis
            report.model_info = gen.model_info or None
        db.add(report)
        db.commit()

    # (3) triage
    triage: dict | None = None
    outcome = None
    try:
        outcome = await clinical.triage_detailed(draft, text, ctx)
        triage = _triage_dict(outcome)
    except Exception as exc:
        errors.append(f"triage failed: {_err(exc)}")
        log.warning("triage failed for patient %s: %s", patient_id, _err(exc))
        triage = _fallback_triage(text, draft.captured if draft else None, _err(exc))
    triage_row = None
    session = db.get(models.CallSession, call_session_id) if call_session_id else None
    if report is not None:
        try:
            triage_row = models.TriageDecision(
                call_session_id=call_session_id, patient_id=patient_id, source="pipeline",
                urgency=triage["urgency"], recommended_action=triage["recommended_action"],
                rationale=triage["rationale"], rule_hits=triage["rule_hits"],
                llm_urgency=triage["llm_urgency"],
                uncertain_because=triage["uncertain_because"], doctor_reviewed=False)
            db.add(triage_row)
            report.escalated = triage["urgency"] in ("urgent", "emergency") or (
                session is not None and session.status == "escalated")
            db.commit()
        except Exception as exc:
            db.rollback()
            triage_row = None
            errors.append(f"saving triage failed: {_err(exc)}")

    # (4) vitals write-back (only values the patient stated; nulls skipped)
    if report is not None and draft is not None:
        try:
            db.execute(delete(models.Vital).where(models.Vital.call_session_id == call_session_id,
                                                  models.Vital.source == source))
            captured = draft.captured.model_dump()
            for field, (vtype, unit) in _VITAL_FIELDS.items():
                value = captured.get(field)
                if value is None:
                    continue
                db.add(models.Vital(patient_id=patient_id, type=vtype, value=float(value),
                                    unit=unit, source=source, call_session_id=call_session_id,
                                    recorded_at=timeutil.utcnow()))
            db.commit()
        except Exception as exc:
            db.rollback()
            errors.append(f"vitals write-back failed: {_err(exc)}")

    # (5) risk proposal: stored on the report, NEVER applied to the patient
    proposed = None
    try:
        if outcome is not None:
            proposed = clinical.propose_risk_level(ctx, draft, outcome.result)
    except Exception as exc:
        errors.append(f"risk proposal failed: {_err(exc)}")

    if report is not None:
        try:
            report.proposed_risk_level = proposed
            report.status = "partial" if errors else "ok"
            report.error = "; ".join(errors)[:2000] if errors else None
            db.commit()
        except Exception:
            db.rollback()
            log.exception("could not finalize report %s", report.id)

    # (6) notify the doctor
    if notify and report is not None:
        name = ctx.name
        urgent = bool(report.escalated)
        summary = report.summary or ""
        await _notify(
            patient_id=patient_id, urgent=urgent,
            subject=(f"{'URGENT: ' if urgent else ''}Guardian check-in report for {name}"),
            body=(f"{summary}\nTriage: {triage['urgency'] if triage else 'n/a'} / "
                  f"{triage['recommended_action'] if triage else 'n/a'}. Draft for doctor "
                  f"review (report {report.id}, call session {call_session_id})."))

    return {
        "report": draft.model_dump() if draft is not None else None,
        "report_error": report_error,
        "triage": triage,
        "saved": ({"report_id": report.id, "triage_id": triage_row.id if triage_row else None}
                  if report is not None else None),
        "errors": errors,
    }


async def handle_post_call(session_id: int, *, force: bool = False) -> None:
    """Background job: own DB session; never raises."""
    db = database.SessionLocal()
    try:
        await _post_call(db, session_id, force=force)
    except Exception:
        log.exception("post-call pipeline crashed for session %s", session_id)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


async def _post_call(db: Session, session_id: int, *, force: bool) -> None:
    session = db.get(models.CallSession, session_id)
    if session is None:
        log.warning("handle_post_call: session %s not found", session_id)
        return
    text = _combined_text(db, session.id)
    patient = _patient(db, session)

    if patient is None:  # unknown inbound caller: hand the doctor the snippet
        urgent = False
        try:
            urgent = bool(redflags.scan(text))
        except Exception:
            log.debug("red-flag scan unavailable for unknown caller", exc_info=True)
        await _notify(patient_id=None, urgent=urgent,
                      subject=f"Guardian call from unknown number {session.caller_number or ''}".strip(),
                      body=(text[:_SNIPPET_CHARS] or "(empty transcript)"))
        if session.status != "escalated":
            session.status = "completed"
        session.ended_at = session.ended_at or timeutil.utcnow()
        db.commit()
        return

    if not transcripts.has_patient_response(text):
        log.info("session %s: no patient response (voicemail/silence) -> missed", session.id)
        await _handle_missed(db, session, "no patient response (voicemail or silence)")
        return

    # Structured patient reports are independent of AI report prose/clinical interpretation.
    from app.tracking import extract_observations
    try:
        await extract_observations(db, session.id, text)
    except Exception:
        db.rollback()
        log.warning("Tracking extraction unavailable for session %s", session.id)

    if not force:
        done = db.scalars(select(models.CallReport.id)
                          .where(models.CallReport.call_session_id == session.id,
                                 models.CallReport.status == "ok")).first()
        if done is not None:
            log.info("session %s already has report %s; skipping", session.id, done)
            return

    await analyze_transcript(db, patient.id, text, call_session_id=session.id, source="call")


# --- inbound voice -----------------------------------------------------------------

async def handle_inbound_voice_init(db: Session, payload: dict) -> dict:
    """Fast path (delays call pickup): DB lookups only, no LLM."""
    raw_caller = str(payload.get("caller_id") or "").strip()
    caller = normalize_e164(raw_caller)
    patient = None
    if caller:
        patient = db.scalars(select(models.Patient).where(models.Patient.phone == caller)
                             .order_by(models.Patient.id)).first()
    call_sid = str(payload.get("call_sid") or "").strip()[:64] or None
    session = models.CallSession(
        patient_id=patient.id if patient else None, direction="inbound", channel="voice",
        status="in_progress", caller_number=(caller or raw_caller)[:32] or None,
        call_sid=call_sid, started_at=timeutil.utcnow(), attempt=1)
    db.add(session)
    db.flush()
    db.add(models.CallAttempt(call_session_id=session.id, attempt=1, call_sid=call_sid,
                              resume_from="start", status="placed"))

    if patient is None:
        variables = context_builder.unknown_caller_variables(session.id)
    else:
        questions = [INBOUND_OPENING] + [
            f"Optional follow-up: {q}" for q in context_builder.doctor_questions_for(db, patient.id)]
        variables = {
            "patient_name": (patient.name or "").strip() or "caller",
            "question_plan": render_plan(_fallback_plan(questions)),
            "last_call_summary": context_builder.last_call_summary(
                context_builder.load_last_reports(db, patient.id)),
            "session_id": str(session.id),
            "resume_from": "start",
        }
    db.commit()
    log.info("inbound call: session %s, known patient=%s", session.id, patient is not None)
    return {
        "type": "conversation_initiation_client_data",
        "dynamic_variables": {k: str(variables.get(k, "")) for k in
                              elevenlabs.REQUIRED_DYNAMIC_VARIABLES},
    }
