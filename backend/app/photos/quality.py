"""Technical image quality only: no diagnosis and no gate on patient uploads."""
import base64
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import database, timeutil
from app.adapters import gemma_client
from app.config import settings
from app.database import Base, JSONType
from app.photos.models import PhotoAsset
from app.photos.storage import private_path

TechnicalIssue = Literal["blur", "too_dark", "glare", "framing", "too_far", "obstructed"]


class QualityResult(BaseModel):
    status: Literal["clear_enough", "retake_recommended", "uncertain"]
    issues: list[TechnicalIssue] = Field(default_factory=list, max_length=6)


class PhotoQuality(Base):
    __tablename__ = "photo_quality_checks"
    request_id: Mapped[str] = mapped_column(ForeignKey("photo_requests.id"), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="checking")
    issues: Mapped[list] = mapped_column(JSONType, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=timeutil.utcnow)


async def assess_photo_quality(images_b64: list[str]) -> QualityResult:
    return await gemma_client.chat_json(settings.gemma_model, [
        {"role": "system", "content": "Assess ONLY technical photograph quality: focus, light, glare, "
         "framing, distance and obstruction. Never diagnose, assess disease, identify body conditions, "
         "or give clinical advice. Return only the technical issue codes and quality status. "
         "If uncertain say uncertain. A retake is optional, never reject an upload."},
        {"role": "user", "content": "Is this photograph technically clear enough for a human to review?"},
    ], QualityResult, images_b64=images_b64)


async def check_quality(request_id: str, digest: str, bind=None):
    with Session(bind=bind if bind is not None else database.engine) as db:
        asset = db.scalar(select(PhotoAsset).where(PhotoAsset.request_id == request_id))
        if not asset or asset.content_hash != digest:
            return
        try:
            image = base64.b64encode(private_path(asset.storage_key).read_bytes()).decode("ascii")
            result = await assess_photo_quality([image])
            status, issues = result.status, result.issues
        except Exception:
            status, issues = "failed", []
        # A slow assessment of an original must not overwrite a newer retake.
        db.expire_all()
        quality = db.get(PhotoQuality, request_id)
        if quality and quality.content_hash == digest:
            quality.status, quality.issues = status, issues
            quality.updated_at = timeutil.utcnow()
            db.commit()


def quality_out(db, request_id):
    quality = db.get(PhotoQuality, request_id)
    if not quality:
        return {"status": "waiting_for_upload", "technical_feedback": []}
    if quality.status == "checking" and quality.updated_at < timeutil.utcnow() - timedelta(minutes=5):
        quality.status = "failed"  # Interrupted task; upload remains available to the doctor.
        db.commit()
    return {"status": quality.status, "technical_feedback": quality.issues,
            "retake_optional": True}
