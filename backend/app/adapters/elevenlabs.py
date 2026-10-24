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

Additions (backwards compatible):

    class InvalidCallRequest(ElevenLabsError, ValueError)
        Bad input (number not E.164, required dynamic variable missing or
        None). Raised before any network call, in mock mode too, so offline
        runs catch the same bugs as real calls.
    set_transport(transport | None)     tests inject httpx.MockTransport
    SIGNATURE_HEADER                    "elevenlabs-signature"
    WEBHOOK_TOLERANCE_S                 30 min, applied to the past and future

Implementation notes:
- `to_number` is normalized to E.164 (spaces/dashes stripped, 00 -> +; a
  missing country code is never guessed) and sent normalized.
- Dynamic variable values are coerced to str; system__* keys are reserved
  by ElevenLabs and are dropped with a warning, never sent.
- Webhooks are checked by our own constant-time HMAC + timestamp window
  first, then by the official SDK helper (`construct_event`), which also
  parses the JSON. If the SDK helper is unavailable, the built-in check
  alone decides; it is never skipped.
- Phone numbers are masked in logs; the API key never appears in logs or
  error messages.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from app import phone
from app.config import settings

try:  # Official SDK webhook helper, preferred by ELEVENLABS_TWILIO_SETUP.md §5.
    from elevenlabs.client import ElevenLabs as _SdkClient
except Exception:  # pragma: no cover - SDK missing/broken: built-in check only
    _SdkClient = None

log = logging.getLogger(__name__)

REQUIRED_DYNAMIC_VARIABLES = (
    "patient_name",
    "question_plan",
    "last_call_summary",
    "session_id",
    "resume_from",
)

SIGNATURE_HEADER = "elevenlabs-signature"
OUTBOUND_CALL_PATH = "/v1/convai/twilio/outbound-call"
OUTBOUND_CALL_TIMEOUT_S = 30.0
# Signed events older than this are rejected (the SDK's window). The same
# bound applies to timestamps in the future (clock-skew sanity check).
WEBHOOK_TOLERANCE_S = 30 * 60

_RESERVED_PREFIX = "system__"
_MAX_DETAIL_CHARS = 300
_MAX_TIMESTAMP_DIGITS = 12  # unix seconds; 10 digits until the year 2286
_UNAVAILABLE = object()

_transport: httpx.AsyncBaseTransport | None = None
_sdk_helper: Any = None  # cached SDK construct_event, or _UNAVAILABLE


class ElevenLabsError(Exception):
    """ElevenLabs API call failed or returned success=false."""


class InvalidCallRequest(ElevenLabsError, ValueError):
    """place_outbound_call input is invalid (raised before any network call)."""


class WebhookVerificationError(Exception):
    """Webhook signature missing, invalid, or outside the tolerance window."""


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Send outbound HTTP through `transport` (tests: httpx.MockTransport).
    None restores the default network transport."""
    global _transport
    _transport = transport


def is_mock() -> bool:
    """Calls are simulated when MOCK_TELEPHONY is set or credentials are missing."""
    return settings.mock_telephony or not settings.eleven_configured


# --- outbound calls -------------------------------------------------------------

async def place_outbound_call(
    to_number: str,
    dynamic_variables: dict[str, str],
    *,
    max_duration_seconds: int | None = None,
    branch_id: str | None = None,
) -> dict:
    """Ask ElevenLabs to call `to_number` from the agent's imported Twilio
    number. Returns {"conversation_id", "call_sid", "mock"}."""
    number = phone.normalize_e164(to_number) if isinstance(to_number, str) else None
    if number is None:
        raise InvalidCallRequest(
            f"to_number must be E.164 (e.g. +4915112345678), got {_mask_phone(to_number)!r}")
    variables = _prepare_dynamic_variables(dynamic_variables)
    if max_duration_seconds is not None and not 1 <= max_duration_seconds <= 3600:
        raise InvalidCallRequest("Call duration override must be between 1 and 3600 seconds")

    if is_mock():
        return _mock_call(number, variables)

    payload = {
        "agent_id": settings.eleven_agent_id,
        "agent_phone_number_id": settings.eleven_agent_phone_number_id,
        "to_number": number,
        "conversation_initiation_client_data": {"dynamic_variables": variables},
    }
    initiation = payload["conversation_initiation_client_data"]
    if branch_id:
        initiation["branch_id"] = branch_id
    if max_duration_seconds is not None:
        initiation["conversation_config_override"] = {
            "conversation": {"max_duration_seconds": max_duration_seconds}
        }
    url = settings.eleven_base_url.rstrip("/") + OUTBOUND_CALL_PATH
    headers = {"xi-api-key": settings.eleven_api_key or "", "Accept": "application/json"}
    timeout = httpx.Timeout(OUTBOUND_CALL_TIMEOUT_S, connect=10.0)
    try:
        async with httpx.AsyncClient(transport=_transport, timeout=timeout) as client:
            response = await client.post(url, json=payload, headers=headers)
    # InvalidURL / ValueError: malformed ELEVEN_BASE_URL (not HTTPError subclasses).
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        raise ElevenLabsError(
            f"ElevenLabs outbound call request failed: {type(exc).__name__}: "
            f"{_clean(str(exc))}") from exc

    status = response.status_code
    if status >= 400:
        raise ElevenLabsError(
            f"ElevenLabs outbound call failed: HTTP {status}: {_clean(response.text)}")
    try:
        data = response.json()
    except ValueError as exc:
        raise ElevenLabsError(
            f"ElevenLabs outbound call returned non-JSON (HTTP {status}): "
            f"{_clean(response.text)}") from exc
    if not isinstance(data, dict) or not data.get("success"):
        message = data.get("message") if isinstance(data, dict) else None
        raise ElevenLabsError(
            "ElevenLabs outbound call was not successful: "
            + _clean(str(message) if message else response.text))

    conversation_id = data.get("conversation_id")
    call_sid = data.get("callSid") or data.get("call_sid")
    log.info("ElevenLabs call to %s placed (session_id=%s conversation_id=%s call_sid=%s)",
             _mask_phone(number), variables["session_id"], conversation_id, call_sid)
    return {"conversation_id": conversation_id, "call_sid": call_sid, "mock": False}


def _prepare_dynamic_variables(dynamic_variables: Mapping[str, Any]) -> dict[str, str]:
    """Coerce values to str, drop reserved system__* keys and None values,
    and require every variable the agent defines."""
    if not isinstance(dynamic_variables, Mapping):
        raise InvalidCallRequest("dynamic_variables must be a mapping of name -> value")
    prepared: dict[str, str] = {}
    dropped: list[str] = []
    for key, value in dynamic_variables.items():
        name = str(key)
        if name.lower().startswith(_RESERVED_PREFIX):
            dropped.append(name)
        elif value is not None:
            prepared[name] = value if isinstance(value, str) else str(value)
    if dropped:
        log.warning("Dropped reserved ElevenLabs dynamic variables (system__* cannot be "
                    "overridden): %s", ", ".join(sorted(dropped)))
    missing = [name for name in REQUIRED_DYNAMIC_VARIABLES if name not in prepared]
    if missing:
        raise InvalidCallRequest("missing required dynamic variables: " + ", ".join(missing))
    return prepared


def _mock_call(number: str, variables: dict[str, str]) -> dict:
    if not settings.mock_telephony:
        log.warning("ElevenLabs credentials incomplete (missing %s): placing a MOCK call",
                    ", ".join(_missing_credentials()))
    result = {
        "conversation_id": f"mock-conv-{uuid.uuid4().hex}",
        "call_sid": f"mock-call-{uuid.uuid4().hex}",
        "mock": True,
    }
    log.info("MOCK call to %s (session_id=%s, resume=%s) -> %s", _mask_phone(number),
             variables["session_id"], variables["resume_from"] != "start",
             result["conversation_id"])
    return result


def _missing_credentials() -> list[str]:
    pairs = (
        ("ELEVEN_API_KEY", settings.eleven_api_key),
        ("ELEVEN_AGENT_ID", settings.eleven_agent_id),
        ("ELEVEN_AGENT_PHONE_NUMBER_ID", settings.eleven_agent_phone_number_id),
    )
    return [name for name, value in pairs if not value]


# --- webhooks ---------------------------------------------------------------------

def verify_webhook(raw_body: bytes, signature_header: str | None) -> dict:
    """Verify an ElevenLabs post-call webhook and return the parsed event.
    Pass the raw request body, untouched (never re-serialized JSON)."""
    secret = settings.eleven_webhook_secret
    if not secret:
        log.error("ELEVEN_WEBHOOK_SECRET is not configured; rejecting ElevenLabs webhook")
        raise WebhookVerificationError("webhook secret not configured")
    if not signature_header or not signature_header.strip():
        raise WebhookVerificationError(f"missing {SIGNATURE_HEADER} header")

    body = _as_bytes(raw_body)
    timestamp, candidates = _parse_signature_header(signature_header)
    age_s = int(time.time()) - int(timestamp)
    if age_s > WEBHOOK_TOLERANCE_S:
        raise WebhookVerificationError("signature timestamp is outside the 30 min tolerance")
    if age_s < -WEBHOOK_TOLERANCE_S:
        raise WebhookVerificationError("signature timestamp is too far in the future")
    # Our own check first: constant-time (the SDK compares with ==) and with
    # a future bound (the SDK only rejects stale timestamps).
    expected = _signature_hex(body, secret, timestamp).encode("ascii")
    if not any(hmac.compare_digest(expected, c.encode("utf-8", "replace")) for c in candidates):
        raise WebhookVerificationError("signature does not match the payload")

    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WebhookVerificationError("webhook body is not valid UTF-8") from exc
    construct_event = _sdk_construct_event()
    try:
        if construct_event is not None:
            # Official helper: re-checks scheme, window and HMAC, parses JSON.
            event = construct_event(text, signature_header, secret)
        else:
            event = json.loads(text)
    except Exception as exc:
        raise WebhookVerificationError(f"webhook rejected: {_sdk_error_text(exc)}") from exc
    if not isinstance(event, dict):
        raise WebhookVerificationError("webhook payload is not a JSON object")
    return event


def sign_webhook(raw_body: bytes, secret: str, timestamp: int | None = None) -> str:
    """The `elevenlabs-signature` value ElevenLabs would send for `raw_body`."""
    ts = str(int(time.time()) if timestamp is None else int(timestamp))
    return f"t={ts},v0={_signature_hex(_as_bytes(raw_body), secret, ts)}"


def _parse_signature_header(header: str) -> tuple[str, list[str]]:
    """'t=<unix>,v0=<hex>' -> ('<unix>', ['<hex>', ...])."""
    timestamp: str | None = None
    candidates: list[str] = []
    for part in header.split(","):
        key, sep, value = part.strip().partition("=")
        if not sep:
            continue
        if key == "t":
            timestamp = value.strip()
        elif key == "v0":
            candidates.append(value.strip())
    if not timestamp or not candidates:
        raise WebhookVerificationError(
            f"malformed {SIGNATURE_HEADER} header (expected t=<unix>,v0=<hex>)")
    # Digits only, and short: int() of a huge value would raise (-> 500).
    is_digits = timestamp.isascii() and timestamp.isdigit()
    if not is_digits or len(timestamp) > _MAX_TIMESTAMP_DIGITS:
        raise WebhookVerificationError(f"malformed {SIGNATURE_HEADER} timestamp")
    return timestamp, candidates


def _signature_hex(body: bytes, secret: str, timestamp: str) -> str:
    """hex(HMAC-SHA256(secret, "<timestamp>.<raw body>"))."""
    message = timestamp.encode("ascii") + b"." + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _sdk_construct_event() -> Callable[[str, str, str], Any] | None:
    """The SDK's WebhooksClient.construct_event, built once. Building the
    client makes no network call and needs no real API key."""
    global _sdk_helper
    if _sdk_helper is None:
        helper = None
        if _SdkClient is not None:
            try:
                # The key is never used: construct_event is purely local.
                helper = getattr(_SdkClient(api_key="unused").webhooks, "construct_event", None)
            except Exception:
                log.warning("Could not build the elevenlabs SDK client", exc_info=True)
        if not callable(helper):
            log.warning("elevenlabs SDK construct_event unavailable; "
                        "using built-in webhook verification only")
            helper = _UNAVAILABLE
        _sdk_helper = helper
    return None if _sdk_helper is _UNAVAILABLE else _sdk_helper


def _sdk_error_text(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    detail = getattr(body, "error", None) or getattr(body, "message", None)
    if isinstance(detail, str) and detail:
        return detail
    if isinstance(exc, json.JSONDecodeError):
        return "body is not valid JSON"
    return f"{type(exc).__name__}: {exc}"[:200]


# --- helpers ---------------------------------------------------------------------

def _as_bytes(raw_body: bytes | bytearray | memoryview | str | None) -> bytes:
    if raw_body is None:
        return b""
    if isinstance(raw_body, str):
        return raw_body.encode("utf-8")
    return bytes(raw_body)


def _clean(text: str | None) -> str:
    """Single line, API key scrubbed, truncated: safe for logs, DB and API errors."""
    flat = " ".join(str(text or "").split())
    key = settings.eleven_api_key
    if key:
        flat = flat.replace(key, "***")
    if len(flat) > _MAX_DETAIL_CHARS:
        flat = flat[:_MAX_DETAIL_CHARS] + "..."
    return flat or "<empty>"


def _mask_phone(number: object) -> str:
    """'+4915112345678' -> '+49*******5678' so logs never carry full numbers."""
    s = "" if number is None else str(number)
    if len(s) <= 7:
        return "*" * len(s)
    return s[:3] + "*" * (len(s) - 7) + s[-4:]
