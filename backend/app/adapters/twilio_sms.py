"""Twilio SMS adapter (send + inbound signature validation).

CONTRACT (implemented by the telephony-adapters task):

    is_mock() -> bool        settings.mock_telephony or not settings.twilio_configured

    async send_sms(db: Session, to: str, body: str, *, kind: str,
                   patient_id: int | None = None) -> models.SmsLog
        Twilio SDK with API-key auth: Client(api_key_sid, api_key_secret,
        account_sid); the blocking SDK call runs in a worker thread.
        ALWAYS writes an sms_log row (status sent|mock|failed, provider_sid,
        error) and commits it. Never raises on delivery failure (logs + row
        with status=failed) so callers in webhooks stay fast and 200.

    validate_twilio_request(url: str, form_params: dict, signature: str | None) -> bool
        twilio.request_validator.RequestValidator(TWILIO_AUTH_TOKEN).validate.
        False if the token is unset or the signature is missing.

    public_webhook_url(path: str, query: str = "") -> str
        The URL Twilio signed: settings.public_base_url (no trailing slash)
        + path (+ "?" + query). Behind Caddy/ngrok request.url is the
        internal URL, so never validate against request.url.

Implementation notes:
- `to` is normalized to E.164 first (spaces/dashes/parens stripped, 00 -> +;
  a missing country code is never guessed). Unusable numbers and empty
  bodies are logged as status=failed without an API call.
- send_sms commits the caller's session (pending changes included); only a
  failing commit raises, after a rollback. sent_at is set explicitly to
  naive UTC rather than relying on the DB server's now().
- `_client_factory` is the seam that builds the Twilio REST client; tests
  swap it for a fake, so no test touches the network.
- `form_params` may be a dict, a starlette FormData / multi-dict, or a
  parse_qs-style dict of lists (repeated keys are kept).
- Phone numbers are masked in logs; Twilio secrets never appear in the
  stored error text.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy.orm import Session
from twilio.base.exceptions import TwilioRestException
from twilio.request_validator import RequestValidator

from app import models, phone, timeutil
from app.config import settings

log = logging.getLogger(__name__)

SMS_TIMEOUT_S = 15.0  # per HTTP request to the Twilio REST API
_MAX_ERROR_CHARS = 500
_LOG_PREVIEW_CHARS = 160


def _default_client_factory() -> Any:
    """Twilio REST client with API-key auth: Client(key_sid, key_secret, account_sid)."""
    # Imported lazily: twilio.rest is slow to import and only real sends need it.
    from twilio.http.http_client import TwilioHttpClient
    from twilio.rest import Client

    return Client(
        settings.twilio_api_key_sid,
        settings.twilio_api_key_secret,
        settings.twilio_account_sid,
        http_client=TwilioHttpClient(timeout=SMS_TIMEOUT_S),
    )


# Seam for tests: monkeypatch with a factory that returns a fake client.
_client_factory: Callable[[], Any] = _default_client_factory


def is_mock() -> bool:
    """SMS are simulated when MOCK_TELEPHONY is set or Twilio creds are missing."""
    return settings.mock_telephony or not settings.twilio_configured


async def send_sms(db: Session, to: str, body: str, *, kind: str,
                   patient_id: int | None = None) -> models.SmsLog:
    """Send one SMS and record it in sms_log. Delivery problems end up as
    status=failed with the reason in `error`; they never raise.

    Commits `db`, so the caller's pending changes are committed with the
    row. Only a failing commit raises (after a rollback)."""
    number = phone.normalize_e164(to) if isinstance(to, str) else None
    text = "" if body is None else str(body)
    row = models.SmsLog(
        patient_id=patient_id,
        direction="outbound",
        to_number=_clip(number or to, 32),
        from_number=_clip(settings.twilio_number, 32),
        body=text,
        kind=_clip(kind, 24) or "other",
        sent_at=timeutil.utcnow(),
    )

    if number is None:
        row.status = "failed"
        row.error = "invalid destination number (expected E.164, e.g. +4915112345678)"
        log.warning("SMS (%s) not sent: invalid destination %s", row.kind, _mask_phone(to))
    elif not text.strip():
        row.status = "failed"
        row.error = "empty message body"
        log.warning("SMS (%s) to %s not sent: empty body", row.kind, _mask_phone(number))
    elif is_mock():
        if not settings.mock_telephony:
            log.warning("Twilio credentials incomplete (missing %s): SMS is MOCKED",
                        ", ".join(_missing_credentials()))
        row.status = "mock"
        log.info("MOCK SMS (%s) to %s: %s", row.kind, _mask_phone(number), _preview(text))
    else:
        try:
            message = await asyncio.to_thread(
                _create_message, number, settings.twilio_number, text)
        except Exception as exc:  # delivery failure: record it, never raise
            row.status = "failed"
            row.error = _error_text(exc)
            log.warning("SMS (%s) to %s failed: %s", row.kind, _mask_phone(number), row.error,
                        exc_info=not isinstance(exc, TwilioRestException))
        else:
            row.status = "sent"
            row.provider_sid = _clip(getattr(message, "sid", None), 64)
            log.info("SMS (%s) sent to %s (sid=%s)", row.kind, _mask_phone(number),
                     row.provider_sid)

    db.add(row)
    try:
        db.commit()
    except Exception:
        # A broken DB is not a delivery failure: roll back and let it surface.
        db.rollback()
        log.exception("Could not write sms_log row (kind=%s, status=%s)", row.kind, row.status)
        raise
    return row


def validate_twilio_request(url: str, form_params: Mapping[str, Any] | None,
                            signature: str | None) -> bool:
    """True only if `signature` (X-Twilio-Signature) matches `url` + params
    under TWILIO_AUTH_TOKEN. Mock mode never bypasses this."""
    token = settings.twilio_auth_token
    if not token:
        log.error("TWILIO_AUTH_TOKEN is not configured; rejecting Twilio webhook")
        return False
    if not signature or not url:
        return False
    try:
        return bool(RequestValidator(token).validate(url, _param_lists(form_params), signature))
    except Exception:
        log.warning("Twilio signature validation errored; treating as invalid", exc_info=True)
        return False


def public_webhook_url(path: str, query: str = "") -> str:
    """The public URL Twilio requested (and signed) for `path` (+ query)."""
    base = settings.public_base_url.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    query = query.lstrip("?")
    return f"{base}{path}?{query}" if query else f"{base}{path}"


# --- helpers -------------------------------------------------------------------

def _create_message(to: str, from_: str | None, body: str) -> Any:
    """Blocking Twilio REST call; runs in a worker thread."""
    client = _client_factory()
    return client.messages.create(to=to, from_=from_, body=body)


class _ParamLists(dict):
    """{name: [values]} that RequestValidator reads through getlist()."""

    def getlist(self, key: str) -> list[str]:
        return self[key]


def _param_lists(form_params: Mapping[str, Any] | None) -> _ParamLists:
    """Normalize dict / multi-dict / dict-of-lists params to string lists."""
    out = _ParamLists()
    if not form_params:
        return out
    if hasattr(form_params, "multi_items"):  # starlette FormData / MultiDict
        items = list(form_params.multi_items())
    else:
        items = []
        for key, value in form_params.items():
            values = value if isinstance(value, (list, tuple)) else [value]
            items.extend((key, v) for v in values)
    for key, value in items:
        out.setdefault(str(key), []).append("" if value is None else str(value))
    return out


def _missing_credentials() -> list[str]:
    pairs = (
        ("TWILIO_ACCOUNT_SID", settings.twilio_account_sid),
        ("TWILIO_API_KEY_SID", settings.twilio_api_key_sid),
        ("TWILIO_API_KEY_SECRET", settings.twilio_api_key_secret),
        ("TWILIO_NUMBER", settings.twilio_number),
    )
    return [name for name, value in pairs if not value]


def _error_text(exc: Exception) -> str:
    """Short, single-line failure reason for sms_log.error (no secrets)."""
    if isinstance(exc, TwilioRestException):
        code = exc.code if exc.code is not None else "?"
        text = f"Twilio error {code} (HTTP {exc.status}): {exc.msg}"
    else:
        text = f"{type(exc).__name__}: {exc}"
    for secret in (settings.twilio_api_key_secret, settings.twilio_auth_token):
        if secret:
            text = text.replace(secret, "***")
    return " ".join(text.split())[:_MAX_ERROR_CHARS]


def _clip(value: object, limit: int) -> str | None:
    """Fit a value into a VARCHAR(limit) column (Postgres rejects overflow)."""
    if value is None or value == "":
        return None
    return str(value)[:limit]


def _preview(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= _LOG_PREVIEW_CHARS else flat[:_LOG_PREVIEW_CHARS] + "..."


def _mask_phone(number: object) -> str:
    """'+4915112345678' -> '+49*******5678' so logs never carry full numbers."""
    s = "" if number is None else str(number)
    if len(s) <= 7:
        return "*" * len(s)
    return s[:3] + "*" * (len(s) - 7) + s[-4:]
