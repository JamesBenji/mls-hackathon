"""Doctor notifications.

Email + PDF are separate tasks (out of scope for the telephony/clinical
work), so this only logs. Keep the signature stable; the email task plugs
in here.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


async def notify_doctor(
    *, patient_id: int | None, subject: str, body: str, urgent: bool = False
) -> None:
    # TODO(email): send via SendGrid/Resend with dashboard link + PDF.
    level = logging.WARNING if urgent else logging.INFO
    log.log(level, "notify_doctor patient=%s urgent=%s subject=%r body=%r",
            patient_id, urgent, subject, body)
