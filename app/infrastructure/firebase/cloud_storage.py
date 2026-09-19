"""Cloud Storage adapter for durable uploaded-image persistence."""
import logging
import uuid
from urllib.parse import quote

logger = logging.getLogger(__name__)


def upload_image(record_id: str, image_kind: str, content: bytes) -> str | None:
    """Upload an image and return a durable browser-accessible download URL.

    ``None`` means Cloud Storage was unavailable, allowing the caller to use
    the local offline cache without losing the detection.
    """
    if not content:
        return None
    try:
        from firebase_admin import storage  # noqa: PLC0415

        bucket = storage.bucket()
        blob = bucket.blob(f"detections/{record_id}/{image_kind}.jpg")
        download_token = str(uuid.uuid4())
        blob.metadata = {"firebaseStorageDownloadTokens": download_token}
        blob.upload_from_string(content, content_type="image/jpeg")
        blob.cache_control = "private, max-age=3600"
        blob.patch()
        return (
            f"https://firebasestorage.googleapis.com/v0/b/{bucket.name}/o/"
            f"{quote(blob.name, safe='')}?alt=media&token={download_token}"
        )
    except Exception as error:
        logger.warning("Cloud Storage upload failed for %s/%s: %s", record_id, image_kind, error)
        return None
