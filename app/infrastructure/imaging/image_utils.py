"""
app/infrastructure/imaging/image_utils.py — Image file persistence utilities.

Saves original upload bytes (preserving EXIF/XMP) and writes OpenCV-annotated
previews to the static directories.
"""
import logging

import cv2
import numpy as np

from app.core.config import ANNOTATED_IMAGE_DIR, UPLOAD_IMAGE_DIR

logger = logging.getLogger(__name__)


def save_upload_image_assets(
    record_id: str,
    original_bytes: bytes,
    annotated_image: np.ndarray | None = None,
) -> dict[str, str]:
    """Persist the exact uploaded file plus the generated annotated preview.

    The source image is written from its raw upload bytes, not re-encoded by
    OpenCV, so EXIF/XMP metadata (including DJI flight and GPS data) is preserved.
    """
    image_urls: dict[str, str] = {}

    try:
        original_path = UPLOAD_IMAGE_DIR / f"{record_id}.jpg"
        original_path.write_bytes(original_bytes)
        image_urls["image_url"] = f"/static/uploads/{record_id}.jpg"
    except Exception as error:
        logger.warning("Could not save original upload image for %s: %s", record_id, error)

    if annotated_image is not None:
        try:
            annotated_path = ANNOTATED_IMAGE_DIR / f"{record_id}.jpg"
            cv2.imwrite(str(annotated_path), annotated_image, [cv2.IMWRITE_JPEG_QUALITY, 92])
            image_urls["annotated_image_url"] = f"/static/annotated_uploads/{record_id}.jpg"
        except Exception as error:
            logger.warning("Could not save annotated image for %s: %s", record_id, error)

    return image_urls
