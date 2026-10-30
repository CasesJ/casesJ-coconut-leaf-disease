"""Image encoding and durable evidence upload helpers."""
from __future__ import annotations

from io import BytesIO
import logging
import time

import numpy as np
from PIL import Image, UnidentifiedImageError

from app.infrastructure.cloudinary_storage import (
    CloudinaryStorageError,
    upload_asset,
)

logger = logging.getLogger(__name__)


def _image_content_type(content: bytes) -> str:
    try:
        with Image.open(BytesIO(content)) as image:
            if image.format == "JPEG":
                return "image/jpeg"
            if image.format == "PNG":
                return "image/png"
            if image.format in {"WEBP", "BMP", "TIFF"}:
                return Image.MIME[image.format]
    except (UnidentifiedImageError, OSError):
        pass
    raise CloudinaryStorageError("Detection original must be JPEG, PNG, WebP, BMP or TIFF")


def save_upload_image_assets(
    record_id: str,
    original_bytes: bytes,
    annotated_image: np.ndarray | None = None,
    timings: dict[str, float] | None = None,
) -> dict:
    """Upload the byte-identical source image as one authenticated asset.

    Keeping one upload avoids duplicate provider work and preserves image quality,
    EXIF/XMP metadata, and the exact evidence bytes used by the detector.
    Annotation previews are rendered from Firestore metadata in the client.
    ``annotated_image`` remains an accepted argument for callers during the
    transition, but is intentionally ignored. The function never writes local
    files or returns provider URLs.
    """
    del annotated_image
    if not record_id:
        raise CloudinaryStorageError("Detection record identity is required")

    validation_started = time.perf_counter()
    content_type = _image_content_type(original_bytes)
    if timings is not None:
        timings["image_validation_ms"] = (time.perf_counter() - validation_started) * 1000
    upload_started = time.perf_counter()
    original = upload_asset(original_bytes, content_type, "detection-original")
    upload_ms = (time.perf_counter() - upload_started) * 1000
    if timings is not None:
        timings["cloudinary_upload_ms"] = upload_ms
    logger.info("Detection asset upload bytes=%d cloudinary_ms=%.0f", len(original_bytes), upload_ms)
    return {"image_assets": {"original": original}}
