from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models as clinical
from app.database import get_db
from app.photos import push
from app.photos.auth import current_credential, doctor_access, issue_credential, patient_access
from app.photos.models import AccessCredential, PatientDevice, PhotoAsset, PhotoRequest, utcnow
from app.photos.quality import PhotoQuality, check_quality, quality_out
from app.photos.storage import decode_image, private_path


def private_response(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(tags=["photo-sync"], dependencies=[Depends(private_response)])
Db = Annotated[Session, Depends(get_db)]
Patient = Annotated[AccessCredential, Depends(patient_access)]
Doctor = Annotated[AccessCredential, Depends(doctor_access)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


class DeviceIn(BaseModel):
    fcm_token: str | None = Field(default=None, min_length=1, max_length=2048, pattern=r"^[!-~]+$")
    platform: Literal["android", "ios", "web"]


class RequestIn(BaseModel):
    instructions: str = Field(min_length=1, max_length=2000)


def owned_patient(db: Session, patient_id: int, doctor_id: int) -> clinical.Patient:
    patient = db.scalar(
        select(clinical.Patient)
        .where(
            clinical.Patient.id == patient_id,
            clinical.Patient.doctor_id == doctor_id,
        )
        # NO KEY UPDATE serializes mutations without blocking upload FK checks.
        .with_for_update(key_share=True)
    )
    if patient is None:
        raise HTTPException(404, "Patient not found")
    return patient


def owned_request(db: Session, request_id: str, patient_id: int) -> PhotoRequest:
    request = db.scalar(
        select(PhotoRequest)
        .where(
            PhotoRequest.id == request_id,
            PhotoRequest.patient_id == patient_id,
        )
        .with_for_update()
    )
    if request is None:
        raise HTTPException(404, "Request not found")
    return request


def photo_out(photo: PhotoAsset) -> dict:
    return {
        "id": photo.id,
        "request_id": photo.request_id,
        "patient_id": photo.patient_id,
        "mime_type": photo.mime_type,
        "size_bytes": photo.size_bytes,
        "created_at": photo.created_at,
        "content_url": f"/photos/{photo.id}/content",
    }


def request_out(db: Session, request: PhotoRequest) -> dict:
    photo = db.scalar(select(PhotoAsset).where(PhotoAsset.request_id == request.id))
    return {
        "id": request.id,
        "patient_id": request.patient_id,
        "instructions": request.instructions,
        "status": request.status,
        "push_status": request.push_status,
        "push_attempts": request.push_attempts,
        "created_at": request.created_at,
        "completed_at": request.completed_at,
        "photo": photo_out(photo) if photo else None,
        "quality": quality_out(db, request.id),
    }


@router.get("/me")
def me(db: Db, credential: Patient):
    patient = db.get(clinical.Patient, credential.patient_id)
    if patient is None:
        raise HTTPException(401, "Patient no longer exists")
    return {"id": patient.id, "name": patient.name, "credential_expires_at": credential.expires_at}


@router.post("/patients/{patient_id}/pairing", status_code=201)
def pair_patient(patient_id: int, db: Db, credential: Doctor):
    """Doctor verifies patient identity in person, then transfers this secret once."""
    owned_patient(db, patient_id, credential.doctor_id)
    token, access = issue_credential(db, "patient", patient_id)
    db.commit()
    return {
        "access_token": token,
        "token_type": "bearer",
        "patient_id": patient_id,
        "expires_at": access.expires_at,
    }


@router.delete("/patients/{patient_id}/pairing", status_code=204)
def revoke_pairing(patient_id: int, db: Db, credential: Doctor):
    owned_patient(db, patient_id, credential.doctor_id)
    db.execute(
        update(AccessCredential)
        .where(
            AccessCredential.patient_id == patient_id,
        )
        .values(revoked=True)
    )
    db.execute(
        update(PatientDevice)
        .where(
            PatientDevice.patient_id == patient_id,
        )
        .values(fcm_token=None)
    )
    db.commit()


@router.put("/me/devices/{device_id}")
def upsert_device(device_id: str, body: DeviceIn, db: Db, credential: Patient):
    if not 1 <= len(device_id) <= 128:
        raise HTTPException(422, "device_id must contain 1-128 characters")
    # Serialize this patient's upserts; global token uniqueness prevents cross-patient binding.
    db.scalar(
        select(clinical.Patient)
        .where(
            clinical.Patient.id == credential.patient_id,
        )
        .with_for_update()
    )
    if body.fcm_token:
        other = db.scalar(select(PatientDevice).where(PatientDevice.fcm_token == body.fcm_token))
        if other and other.patient_id != credential.patient_id:
            raise HTTPException(
                409, "Token is already registered; revoke the previous registration first"
            )
        if other and other.device_id != device_id:
            other.fcm_token = None
            db.flush()
    device = db.get(PatientDevice, (credential.patient_id, device_id))
    if device is None:
        device = PatientDevice(patient_id=credential.patient_id, device_id=device_id)
        db.add(device)
    device.fcm_token = body.fcm_token
    device.platform = body.platform
    device.updated_at = utcnow()
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Token is already registered") from exc
    return {
        "device_id": device_id,
        "platform": device.platform,
        "push_enabled": device.fcm_token is not None,
        "updated_at": device.updated_at,
    }


@router.get("/me/photo-requests")
def list_requests(db: Db, credential: Patient):
    requests = db.scalars(
        select(PhotoRequest)
        .where(
            PhotoRequest.patient_id == credential.patient_id,
        )
        .order_by(PhotoRequest.created_at.desc())
    ).all()
    return [request_out(db, request) for request in requests]


@router.get("/me/photo-requests/{request_id}")
def get_request(request_id: str, db: Db, credential: Patient):
    return request_out(db, owned_request(db, request_id, credential.patient_id))


@router.post("/patients/{patient_id}/photo-requests", status_code=201)
def create_request(
    patient_id: int, body: RequestIn, db: Db, credential: Doctor, idempotency_key: IdempotencyKey
):
    owned_patient(db, patient_id, credential.doctor_id)
    existing = db.scalar(
        select(PhotoRequest).where(
            PhotoRequest.doctor_id == credential.doctor_id,
            PhotoRequest.idempotency_key == idempotency_key,
        )
    )
    if existing:
        if existing.patient_id != patient_id or existing.instructions != body.instructions:
            raise HTTPException(409, "Idempotency key was used for a different request")
        return request_out(db, existing)
    request = PhotoRequest(
        id=str(uuid4()),
        patient_id=patient_id,
        doctor_id=credential.doctor_id,
        instructions=body.instructions,
        idempotency_key=idempotency_key,
    )
    db.add(request)
    try:
        db.commit()  # Durable before any Firebase work.
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Concurrent request; retry with the same idempotency key") from exc
    push.deliver(db, request)
    return request_out(db, request)


@router.post("/patients/{patient_id}/photo-requests/{request_id}/retry-push")
def retry_push(patient_id: int, request_id: str, db: Db, credential: Doctor):
    owned_patient(db, patient_id, credential.doctor_id)
    request = owned_request(db, request_id, patient_id)
    if request.status != "pending":
        raise HTTPException(409, "Request is already completed")
    push.deliver(db, request)
    return request_out(db, request)


@router.post("/me/photo-requests/{request_id}/photos", status_code=201)
def upload_photo(
    request_id: str, file: UploadFile, background: BackgroundTasks,
    db: Db, credential: Patient, idempotency_key: IdempotencyKey
):
    request = owned_request(db, request_id, credential.patient_id)
    encoded, mime, digest = decode_image(file)
    existing = db.scalar(select(PhotoAsset).where(PhotoAsset.request_id == request.id))
    quality = db.get(PhotoQuality, request.id)
    old_path = None
    if existing:
        if existing.idempotency_key == idempotency_key and existing.content_hash == digest:
            return photo_out(existing)
        if (existing.idempotency_key == idempotency_key or not quality
                or quality.status not in ("retake_recommended", "uncertain", "failed")):
            raise HTTPException(409, "Replay original key and bytes, or use a new key for an optional retake")
        old_path = private_path(existing.storage_key)
    asset_id = uuid4()
    path = private_path(f"{asset_id.hex}.jpg")
    created_file = False
    try:
        with path.open("xb") as output:
            created_file = True
            output.write(encoded)
        path.chmod(0o600)
        if existing:
            photo = existing
            photo.idempotency_key = idempotency_key
            photo.content_hash = digest
            photo.storage_key = path.name
            photo.mime_type = mime
            photo.size_bytes = len(encoded)
        else:
            upload = clinical.PhotoUpload(patient_id=credential.patient_id, source="patient_app")
            db.add(upload)
            db.flush()
            photo = PhotoAsset(
                id=str(asset_id),
                request_id=request.id,
                patient_id=credential.patient_id,
                upload_id=upload.id,
                idempotency_key=idempotency_key,
                content_hash=digest,
                storage_key=path.name,
                mime_type=mime,
                size_bytes=len(encoded),
            )
        db.add(photo)
        if quality is None:
            quality = PhotoQuality(request_id=request.id, content_hash=digest)
            db.add(quality)
        quality.content_hash, quality.status, quality.issues = digest, "checking", []
        quality.updated_at = utcnow()
        request.status = "completed"
        request.completed_at = utcnow()
        db.commit()
    except Exception:
        db.rollback()
        if created_file:
            path.unlink(missing_ok=True)
        raise
    if old_path:
        old_path.unlink(missing_ok=True)
    background.add_task(check_quality, request.id, digest, db.get_bind())
    return photo_out(photo)


@router.get("/patients/{patient_id}/photos")
def list_photos(patient_id: int, db: Db, credential: Doctor):
    owned_patient(db, patient_id, credential.doctor_id)
    photos = db.scalars(
        select(PhotoAsset)
        .where(
            PhotoAsset.patient_id == patient_id,
        )
        .order_by(PhotoAsset.created_at.desc())
    ).all()
    return [photo_out(photo) for photo in photos]


@router.get("/photos/{photo_id}/content")
def photo_content(
    photo_id: str, db: Db, credential: Annotated[AccessCredential, Depends(current_credential)]
):
    photo = db.get(PhotoAsset, photo_id)
    if photo is None:
        raise HTTPException(404, "Photo not found")
    if credential.role == "patient":
        if photo.patient_id != credential.patient_id:
            raise HTTPException(404, "Photo not found")
    elif credential.role == "doctor":
        owned_patient(db, photo.patient_id, credential.doctor_id)
    else:
        raise HTTPException(403, "Access denied")
    path = private_path(photo.storage_key)
    if not path.is_file():
        raise HTTPException(404, "Photo not found")
    return FileResponse(
        path,
        media_type=photo.mime_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
