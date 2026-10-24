"""Email delivery via Gmail SMTP (App Password), with a mock fallback.

Uses Python's built-in smtplib — no extra dependencies. If the Gmail
credentials are not configured, logs the email instead of sending so the
flow stays testable offline.

Setup (one time):
  1. Enable 2-Step Verification on the Google account.
  2. Google Account -> Security -> App passwords -> generate one.
  3. Put the address + 16-char password in .env as GMAIL_ADDRESS /
     GMAIL_APP_PASSWORD.
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

from app.config import settings

log = logging.getLogger("guardian.email")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465  # SSL


def is_configured() -> bool:
    return bool(settings.gmail_address and settings.gmail_app_password)


async def send_email(to: str, subject: str, html: str,
                     pdf_path: str | None = None) -> dict:
    """Send an HTML email with an optional PDF attachment via Gmail SMTP."""
    if not is_configured():
        log.info("[MOCK EMAIL] to=%s subject=%s pdf=%s", to, subject, pdf_path)
        return {"status": "mock", "to": to, "subject": subject,
                "pdf": pdf_path}

    msg = EmailMessage()
    msg["From"] = settings.email_from or settings.gmail_address
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content("This report is best viewed as HTML.")
    msg.add_alternative(html, subtype="html")

    if pdf_path and os.path.exists(pdf_path):
        with open(pdf_path, "rb") as fh:
            msg.add_attachment(
                fh.read(), maintype="application", subtype="pdf",
                filename=os.path.basename(pdf_path),
            )

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.login(settings.gmail_address, settings.gmail_app_password)
            smtp.send_message(msg)
        return {"status": "sent", "to": to}
    except Exception as exc:  # noqa: BLE001
        log.warning("gmail send failed: %s", exc)
        return {"status": "error", "detail": str(exc)}
