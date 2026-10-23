"""Cloud Storage adapter for durable uploaded-image persistence."""
import logging
import re
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


def upload_monitoring_evidence(farmer_id: str, case_id: str, follow_up_id: str,
                               kind: str, content: bytes, content_type: str) -> str:
    """Store one new monitoring asset without changing its bytes.

    Object metadata and cache policy are set on the upload request itself.
    """
    from firebase_admin import storage  # noqa: PLC0415

    bucket = storage.bucket()
    path = monitoring_evidence_path(farmer_id, case_id, follow_up_id, kind, content_type)
    blob = bucket.blob(path)
    token = str(uuid.uuid4())
    blob.metadata = {"firebaseStorageDownloadTokens": token}
    blob.cache_control = "private, max-age=3600"
    blob.upload_from_string(content, content_type=content_type)
    return (f"https://firebasestorage.googleapis.com/v0/b/{bucket.name}/o/"
            f"{quote(blob.name, safe='')}?alt=media&token={token}")


def monitoring_evidence_path(farmer_id: str, case_id: str, follow_up_id: str,
                             kind: str, content_type: str) -> str:
    """Return the exact Cloud Storage key for one newly-created evidence asset."""
    if kind not in {"original", "annotated"}:
        raise ValueError("Unsupported monitoring evidence kind")
    if content_type not in {"image/jpeg", "image/png"}:
        raise ValueError("Unsupported monitoring evidence content type")
    suffix = ".png" if content_type == "image/png" else ".jpg"
    return f"monitoring/{farmer_id}/{case_id}/{follow_up_id}/{kind}{suffix}"


def delete_monitoring_evidence(farmer_id: str, case_id: str, follow_up_id: str,
                               kind: str, content_type: str) -> bool:
    """Delete only the exact object created by the current failed upload.

    Callers must invoke this only for evidence that their current request has
    already uploaded. It never lists or deletes a prefix, so historical and
    unrelated images are outside its scope.
    """
    try:
        from firebase_admin import storage  # noqa: PLC0415

        path = monitoring_evidence_path(farmer_id, case_id, follow_up_id, kind, content_type)
        blob = storage.bucket().blob(path)
        blob.delete()
        return True
    except Exception as error:
        logger.warning("Could not clean up new monitoring object %s/%s: %s", case_id, kind, error)
        return False


def tree_reference_path(reference: dict) -> str:
    """Keys are derived from server-owned IDs; never accept a URL or arbitrary key."""
    ids = [reference.get(key) for key in ("farmer_id", "case_id", "id")]
    if any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) for value in ids):
        raise ValueError("Invalid tree reference identity")
    mime = reference.get("content_type")
    if mime not in {"image/jpeg", "image/png"}:
        raise ValueError("Unsupported whole-tree content type")
    suffix = "png" if mime == "image/png" else "jpg"
    return f"monitoring_identity/{ids[0]}/{ids[1]}/{ids[2]}/whole_tree.{suffix}"


def upload_private_tree_reference(reference: dict, content: bytes) -> dict:
    """Create an immutable private object, with no Firebase download token or ACL."""
    from firebase_admin import storage
    path = tree_reference_path(reference)
    blob = storage.bucket().blob(path)
    blob.cache_control = "private, no-store"
    blob.metadata = {"sha256": reference["sha256"]}
    blob.upload_from_string(content, content_type=reference["content_type"], if_generation_match=0)
    return {**reference, "storage_path": path, "storage_generation": str(blob.generation)}


def read_private_tree_reference(reference: dict) -> bytes:
    from firebase_admin import storage
    from app.core.config import MAX_IMAGE_UPLOAD_BYTES
    blob = storage.bucket().blob(tree_reference_path(reference))
    content = blob.download_as_bytes(if_generation_match=int(reference["storage_generation"]),
                                     end=MAX_IMAGE_UPLOAD_BYTES)
    if len(content) > MAX_IMAGE_UPLOAD_BYTES:
        raise ValueError("Stored reference exceeds upload limit")
    return content


def delete_new_tree_reference(reference: dict) -> None:
    """Failure compensation ONLY for a newly uploaded, proven-unreferenced object."""
    try:
        from firebase_admin import storage
        storage.bucket().blob(tree_reference_path(reference)).delete(
            if_generation_match=int(reference["storage_generation"]))
    except Exception:
        logger.warning("Could not clean up a new unreferenced whole-tree upload", exc_info=True)
