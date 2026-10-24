"""Request authentication helpers.

- Dashboard / teammate routes: `X-API-Key: <GUARDIAN_API_KEY>`.
- ElevenLabs agent webhook tools (+ voice-init): `Authorization: Bearer
  <ELEVEN_TOOL_AUTH_TOKEN>`.
- ElevenLabs post-call webhooks and Twilio webhooks authenticate by
  signature (see app/adapters/), never by X-API-Key.

Settings are read at call time so tests can monkeypatch them.
"""
from __future__ import annotations

import hmac
import logging

from fastapi import Header, HTTPException

from app.config import settings

log = logging.getLogger(__name__)


def _matches(given: str | None, expected: str) -> bool:
    return bool(given) and hmac.compare_digest(given.encode(), expected.encode())


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    """FastAPI dependency for all non-webhook routes.

    If GUARDIAN_API_KEY is empty, auth is disabled (local dev); main.py logs
    a loud warning at startup in that case.
    """
    expected = settings.guardian_api_key
    if not expected:
        return
    if not _matches(x_api_key, expected):
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


def bearer_token_ok(authorization: str | None) -> bool:
    """True if `Authorization: Bearer <token>` matches ELEVEN_TOOL_AUTH_TOKEN."""
    expected = settings.eleven_tool_auth_token
    if not expected or not authorization:
        return False
    scheme, _, token = authorization.partition(" ")
    return scheme.lower() == "bearer" and _matches(token.strip(), expected)


def require_tool_token(authorization: str | None = Header(default=None)) -> None:
    """FastAPI dependency for the ElevenLabs agent's webhook tools."""
    if not settings.eleven_tool_auth_token:
        log.error("ELEVEN_TOOL_AUTH_TOKEN is not configured; rejecting tool call")
        raise HTTPException(status_code=401, detail="tool auth not configured")
    if not bearer_token_ok(authorization):
        raise HTTPException(status_code=401, detail="invalid tool token")
