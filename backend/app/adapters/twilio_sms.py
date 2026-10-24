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
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app import models


def is_mock() -> bool:
    raise NotImplementedError


async def send_sms(db: Session, to: str, body: str, *, kind: str,
                   patient_id: int | None = None) -> models.SmsLog:
    raise NotImplementedError


def validate_twilio_request(url: str, form_params: dict, signature: str | None) -> bool:
    raise NotImplementedError


def public_webhook_url(path: str, query: str = "") -> str:
    raise NotImplementedError
