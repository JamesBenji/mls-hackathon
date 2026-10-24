"""Best-effort FCM delivery. No credentials or health information in payloads/logs."""

import logging

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.photos.models import PatientDevice, PhotoRequest

logger = logging.getLogger(__name__)


class DeadToken(Exception):
    pass


def send_notification(token: str, request_id: str) -> None:
    import firebase_admin
    from firebase_admin import messaging

    try:
        firebase_admin.get_app()
    except ValueError:
        firebase_admin.initialize_app(options={"httpTimeout": 10})  # Server ADC only
    try:
        messaging.send(
            messaging.Message(
                token=token,
                data={"type": "photo_request", "requestId": request_id},
                notification=messaging.Notification(
                    title="New request",
                    body="Open the app to view your request.",
                ),
            )
        )
    except (messaging.UnregisteredError, messaging.SenderIdMismatchError) as exc:
        raise DeadToken() from exc


def deliver(db: Session, request: PhotoRequest) -> None:
    """Called only after request commit; explicit doctor retry may duplicate a push."""
    request.push_attempts += 1
    devices = db.scalars(
        select(PatientDevice).where(
            PatientDevice.patient_id == request.patient_id,
            PatientDevice.fcm_token.is_not(None),
        )
    ).all()
    if not settings.photo_push_enabled:
        request.push_status = "disabled"
    elif not devices:
        request.push_status = "no_devices"
    else:
        delivered = 0
        for device in devices:
            attempted_token = device.fcm_token
            try:
                send_notification(attempted_token, request.id)
                delivered += 1
            except DeadToken:
                # Do not erase a freshly rotated token after a slow FCM response.
                db.execute(
                    update(PatientDevice)
                    .where(
                        PatientDevice.patient_id == device.patient_id,
                        PatientDevice.device_id == device.device_id,
                        PatientDevice.fcm_token == attempted_token,
                    )
                    .values(fcm_token=None)
                    .execution_options(synchronize_session=False)
                )
            except Exception:  # noqa: BLE001 -- all provider/config failures must preserve polling
                # Firebase exception text can contain tokens; intentionally omit it.
                logger.warning("Photo push failed; request remains available by polling")
        if delivered == len(devices):
            request.push_status = "delivered"
        elif delivered:
            request.push_status = "partial"
        else:
            request.push_status = "failed"
    db.commit()
