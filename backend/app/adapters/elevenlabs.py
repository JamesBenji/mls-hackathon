"""ElevenLabs Agents adapter (outbound calls via the imported Twilio number,
webhook signature verification).

CONTRACT (implemented by the telephony-adapters task):

    REQUIRED_DYNAMIC_VARIABLES = ("patient_name", "question_plan",
        "last_call_summary", "session_id", "resume_from")

    class ElevenLabsError(Exception)            API error / success=false
    class WebhookVerificationError(Exception)   bad/missing/stale signature

    is_mock() -> bool        settings.mock_telephony or not settings.eleven_configured

    async place_outbound_call(to_number: str, dynamic_variables: dict[str, str])
        -> {"conversation_id": str | None, "call_sid": str | None, "mock": bool}
        POST {ELEVEN_BASE_URL}/v1/convai/twilio/outbound-call, header xi-api-key.
        Validates E.164 + that all REQUIRED_DYNAMIC_VARIABLES are present (all
        values str). Raises ElevenLabsError if success is false / HTTP error.
        Mock mode: logs, returns fake ids "mock-conv-<hex>" / "mock-call-<hex>".

    verify_webhook(raw_body: bytes, signature_header: str | None) -> dict
        Validates `elevenlabs-signature` (t=<unix>,v0=<hex hmac-sha256 of
        "<t>.<raw body>" keyed by ELEVEN_WEBHOOK_SECRET>, 30 min tolerance)
        and returns the parsed JSON event. Raises WebhookVerificationError.
        Never accepts unsigned requests, also not in mock mode.

    sign_webhook(raw_body: bytes, secret: str, timestamp: int | None = None) -> str
        Produces a valid header value (used by tests / local simulation).
"""
from __future__ import annotations

REQUIRED_DYNAMIC_VARIABLES = (
    "patient_name",
    "question_plan",
    "last_call_summary",
    "session_id",
    "resume_from",
)


class ElevenLabsError(Exception):
    """ElevenLabs API call failed or returned success=false."""


class WebhookVerificationError(Exception):
    """Webhook signature missing, invalid, or outside the tolerance window."""


def is_mock() -> bool:
    raise NotImplementedError


async def place_outbound_call(to_number: str, dynamic_variables: dict[str, str]) -> dict:
    raise NotImplementedError


def verify_webhook(raw_body: bytes, signature_header: str | None) -> dict:
    raise NotImplementedError


def sign_webhook(raw_body: bytes, secret: str, timestamp: int | None = None) -> str:
    raise NotImplementedError
