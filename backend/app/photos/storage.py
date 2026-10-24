"""Private local images, decoded and re-encoded to remove metadata and trailers."""

import hashlib
import io
import re
import warnings
from pathlib import Path

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import settings


def decode_image(file: UploadFile) -> tuple[bytes, str, str]:
    raw = file.file.read(settings.photo_max_bytes + 1)
    if len(raw) > settings.photo_max_bytes:
        raise HTTPException(413, "Image exceeds size limit")
    digest = hashlib.sha256(raw).hexdigest()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                mime = {"JPEG": "image/jpeg", "PNG": "image/png"}.get(image.format)
                if mime is None or file.content_type != mime:
                    raise HTTPException(
                        415, "Only matching JPEG or PNG bytes and MIME are accepted"
                    )
                if image.width * image.height > settings.photo_max_pixels:
                    raise HTTPException(413, "Image exceeds pixel limit")
                if getattr(image, "n_frames", 1) != 1:
                    raise HTTPException(415, "Animated images are not supported")
                image.load()
                clean = ImageOps.exif_transpose(image).convert("RGB")
                output = io.BytesIO()
                clean.save(output, format="JPEG", quality=90)
                encoded = output.getvalue()
                if len(encoded) > settings.photo_max_bytes:
                    raise HTTPException(413, "Normalized image exceeds size limit")
                return encoded, "image/jpeg", digest
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise HTTPException(415, "Invalid image") from exc


def private_path(key: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}\.jpg", key):
        raise HTTPException(404, "Photo not found")
    root = Path(settings.photo_storage_dir).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / key
    if path.is_symlink():
        raise HTTPException(404, "Photo not found")
    return path
