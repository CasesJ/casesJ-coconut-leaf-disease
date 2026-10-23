"""Image encoding and durable evidence upload helpers."""
from __future__ import annotations

from io import BytesIO

import numpy as np
from PIL import Image, UnidentifiedImageError

from app.infrastructure.cloudinary_storage import (
    CloudinaryStorageError,
    upload_asset,
)


def _image_content_type(content: bytes) -> str:
    try:
        with Image.open(BytesIO(content)) as image:
            if image.format == "JPEG":
                return "image/jpeg"
            if image.format == "PNG":
                return "image/png"
    except (UnidentifiedImageError, OSError):
        pass
    raise CloudinaryStorageError("Detection original must be a JPEG or PNG image")


def save_upload_image_assets(
    record_id: str,
    original_bytes: bytes,
    annotated_image: np.ndarray | None = None,
) -> dict:
    """Upload the source image as the detection's only new image asset.

    Raw source bytes are preserved to retain EXIF/XMP, including drone GPS.
    Annotation previews are rendered from Firestore metadata in the client and
    are never stored as duplicate image files. ``annotated_image`` remains an
    accepted argument for callers during the transition, but is intentionally
    ignored. The function never writes local files or returns provider URLs.
    """
    del annotated_image
    if not record_id:
        raise CloudinaryStorageError("Detection record identity is required")

    original = upload_asset(original_bytes, _image_content_type(original_bytes), "detection-original")
    return {"image_assets": {"original": original}}
