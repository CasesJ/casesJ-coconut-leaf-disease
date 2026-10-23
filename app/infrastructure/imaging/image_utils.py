"""Image encoding and durable evidence upload helpers."""
from __future__ import annotations

from io import BytesIO

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError

from app.infrastructure.cloudinary_storage import (
    CloudinaryStorageError,
    delete_asset,
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
    """Upload source bytes and the annotated preview as authenticated assets.

    Raw source bytes are preserved to retain EXIF/XMP, including drone GPS.
    A partial upload is compensated by deleting only assets created here. The
    function never writes local files or returns provider delivery URLs.
    """
    if not record_id:
        raise CloudinaryStorageError("Detection record identity is required")

    uploaded: list[dict] = []
    try:
        original = upload_asset(original_bytes, _image_content_type(original_bytes), "detection-original")
        uploaded.append(original)
        assets = {"original": original}

        if annotated_image is not None:
            success, encoded = cv2.imencode(
                ".jpg", annotated_image, [cv2.IMWRITE_JPEG_QUALITY, 92]
            )
            if not success:
                raise CloudinaryStorageError("Could not encode annotated image")
            annotated = upload_asset(encoded.tobytes(), "image/jpeg", "detection-annotated")
            uploaded.append(annotated)
            assets["annotated"] = annotated
        return {"image_assets": assets}
    except Exception:
        for reference in reversed(uploaded):
            delete_asset(reference)
        raise
