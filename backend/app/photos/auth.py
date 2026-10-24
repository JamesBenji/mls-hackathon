"""Opaque random bearer credentials. Only SHA-256 digests reach the database."""

import hashlib
import secrets
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.photos.models import AccessCredential, utcnow

bearer = HTTPBearer(auto_error=False)


def issue_credential(db: Session, role: str, subject_id: int) -> tuple[str, AccessCredential]:
    token = secrets.token_urlsafe(32)
    credential = AccessCredential(
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        role=role,
        patient_id=subject_id if role == "patient" else None,
        doctor_id=subject_id if role == "doctor" else None,
        expires_at=utcnow() + timedelta(days=settings.photo_credential_days),
    )
    db.add(credential)
    db.flush()
    return token, credential


def current_credential(
    authorization: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> AccessCredential:
    if authorization is None:
        raise HTTPException(
            401, "Bearer credential required", headers={"WWW-Authenticate": "Bearer"}
        )
    digest = hashlib.sha256(authorization.credentials.encode()).hexdigest()
    credential = db.scalar(
        select(AccessCredential).where(
            AccessCredential.token_hash == digest,
            AccessCredential.revoked.is_(False),
            AccessCredential.expires_at > utcnow(),
        )
    )
    if credential is None:
        raise HTTPException(
            401, "Invalid or expired credential", headers={"WWW-Authenticate": "Bearer"}
        )
    return credential


def patient_access(
    credential: Annotated[AccessCredential, Depends(current_credential)],
) -> AccessCredential:
    if credential.role != "patient":
        raise HTTPException(403, "Patient credential required")
    return credential


def doctor_access(
    credential: Annotated[AccessCredential, Depends(current_credential)],
) -> AccessCredential:
    if credential.role != "doctor":
        raise HTTPException(403, "Doctor credential required")
    return credential
