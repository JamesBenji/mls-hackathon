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
"""
from __future__ import annotations

from sqlalchemy.orm import Session


class CallStartError(Exception):
    """Placing the outbound call failed."""


async def start_call(db: Session, patient_id: int, *, call_job_id: int | None = None) -> dict:
    raise NotImplementedError
