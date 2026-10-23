"""Firestore-backed detection record persistence and retrieval."""
from __future__ import annotations

import logging
from copy import deepcopy
from threading import RLock
from time import monotonic
from typing import Optional

import numpy as np

from app.domain.detection import _serialize_detection_payload, apply_record_defaults
from app.infrastructure.imaging.image_utils import save_upload_image_assets
from app.infrastructure.cloudinary_storage import CloudinaryStorageError, delete_asset

logger = logging.getLogger(__name__)


class DetectionStoreUnavailableError(RuntimeError):
    """Firestore could not serve an authoritative detection record operation."""


class DetectionPersistenceError(RuntimeError):
    """A new image or detection could not be durably committed."""


EXPERT_RECORDS_CACHE_TTL_SECONDS = 45
_expert_records_cache: dict[str, object] = {"records": None, "limit": 0, "loaded_at": 0.0}
_expert_records_cache_lock = RLock()


def invalidate_expert_records_cache() -> None:
    """Discard the short-lived expert queue cache after a record mutation."""
    with _expert_records_cache_lock:
        _expert_records_cache.update({"records": None, "limit": 0, "loaded_at": 0.0})


def _get_firestore():
    """Return the configured Firestore client, without any local fallback."""
    try:
        from firebase_admin import firestore  # noqa: PLC0415

        return firestore.client(), True
    except Exception as error:
        logger.warning("Firestore client unavailable (%s)", type(error).__name__)
        return None, False


def _require_firestore():
    fs, available = _get_firestore()
    if not available or fs is None:
        raise DetectionStoreUnavailableError("Firestore is unavailable")
    return fs


def _record_ref(fs, user_id: str, record_id: str):
    if (not user_id or not record_id or "/" in user_id or "/" in record_id
            or user_id in {".", ".."} or record_id in {".", ".."}):
        return None
    return (fs.collection("users").document(user_id)
            .collection("detections").document(record_id))


def _record_from_snapshot(snapshot, user_id: str, record_id: str) -> dict | None:
    if not snapshot.exists:
        return None
    record = snapshot.to_dict() or {}
    stored_owner = str(record.get("user_id") or user_id)
    if stored_owner != user_id:
        logger.error("Firestore detection ownership mismatch for document %s", record_id)
        return None
    stored_id = str(record.get("id") or record_id)
    if stored_id != record_id:
        logger.error("Firestore detection identity mismatch for document %s", record_id)
        return None
    record["id"] = record_id
    record.setdefault("user_id", user_id)
    return record


def find_record_for_user(user_id: str, record_id: str) -> dict | None:
    """Read one canonical Firestore detection owned by *user_id*."""
    fs = _require_firestore()
    ref = _record_ref(fs, user_id, record_id)
    if ref is None:
        return None
    try:
        return _record_from_snapshot(ref.get(), user_id, record_id)
    except Exception as error:
        logger.warning("Firestore detection lookup failed (%s)", type(error).__name__)
        raise DetectionStoreUnavailableError("Firestore detection lookup failed") from None


def find_canonical_record_for_user(user_id: str, record_id: str) -> dict | None:
    """Return the Firestore system-of-record for private media authorization."""
    return find_record_for_user(user_id, record_id)


def update_record_everywhere(user_id: str, record_id: str, updates: dict) -> bool:
    """Update only the canonical Firestore detection document."""
    fs = _require_firestore()
    ref = _record_ref(fs, user_id, record_id)
    if ref is None:
        return False
    try:
        snapshot = ref.get()
        record = _record_from_snapshot(snapshot, user_id, record_id)
        if record is None:
            return False
        ref.update(dict(updates))
    except DetectionStoreUnavailableError:
        raise
    except Exception as error:
        logger.warning("Firestore detection update failed (%s)", type(error).__name__)
        raise DetectionStoreUnavailableError("Firestore detection update failed") from None
    invalidate_expert_records_cache()
    return True


def get_all_user_records(limit: int = 500) -> list[dict]:
    """Return the expert queue from the canonical Firestore detections group."""
    safe_limit = max(1, min(int(limit), 2_000))
    now = monotonic()
    with _expert_records_cache_lock:
        cached_records = _expert_records_cache["records"]
        cache_is_fresh = now - float(_expert_records_cache["loaded_at"]) < EXPERT_RECORDS_CACHE_TTL_SECONDS
        if cached_records is not None and cache_is_fresh and int(_expert_records_cache["limit"]) >= safe_limit:
            return deepcopy(cached_records[:safe_limit])

    fs = _require_firestore()
    try:
        from firebase_admin import firestore  # noqa: PLC0415

        query = (fs.collection_group("detections")
                 .order_by("timestamp", direction=firestore.Query.DESCENDING)
                 .limit(safe_limit))
        records: list[dict] = []
        for doc in query.stream():
            record = doc.to_dict() or {}
            segments = tuple(str(doc.reference.path).split("/"))
            if (len(segments) != 4 or segments[0] != "users"
                    or segments[2] != "detections"):
                logger.error("Skipping detection outside the canonical Firestore path (%s)", doc.id)
                continue
            owner_from_path = segments[1]
            owner_from_data = str(record.get("user_id") or owner_from_path)
            record_id_from_data = str(record.get("id") or doc.id)
            if (not owner_from_path or owner_from_data != owner_from_path
                    or record_id_from_data != doc.id):
                logger.error("Skipping Firestore detection with mismatched owner (%s)", doc.id)
                continue
            record["id"] = doc.id
            record["user_id"] = owner_from_path
            record.setdefault("source", "upload")
            record["storage_backend"] = "firestore"
            apply_record_defaults(record)
            records.append(record)
        records.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    except Exception as error:
        logger.warning("Firestore expert detection query failed (%s)", type(error).__name__)
        raise DetectionStoreUnavailableError("Firestore expert queue is unavailable") from None

    records = records[:safe_limit]
    with _expert_records_cache_lock:
        _expert_records_cache.update({
            "records": deepcopy(records),
            "limit": safe_limit,
            "loaded_at": monotonic(),
        })
    return records


def get_user_records(user_id: str, limit: int | None = None) -> list[dict]:
    """Return only the authenticated user's records from Firestore."""
    fs = _require_firestore()
    if not user_id or "/" in user_id:
        return []
    try:
        from firebase_admin import firestore  # noqa: PLC0415

        query = (fs.collection("users").document(user_id)
                 .collection("detections")
                 .order_by("timestamp", direction=firestore.Query.DESCENDING))
        if limit is not None:
            query = query.limit(max(0, int(limit)))
        records: list[dict] = []
        for doc in query.stream():
            record = doc.to_dict() or {}
            stored_owner = str(record.get("user_id") or user_id)
            stored_id = str(record.get("id") or doc.id)
            if stored_owner != user_id or stored_id != doc.id:
                logger.error("Skipping Firestore detection with mismatched owner (%s)", doc.id)
                continue
            record["id"] = doc.id
            record["user_id"] = user_id
            record.setdefault("source", "upload")
            record["storage_backend"] = "firestore"
            apply_record_defaults(record)
            records.append(record)
        records.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
        return records[:limit] if limit is not None else records
    except Exception as error:
        logger.warning("Firestore user detection query failed (%s)", type(error).__name__)
        raise DetectionStoreUnavailableError("Firestore detection history is unavailable") from None


def save_detection_record(
    user_id: str,
    email: str,
    record_id: str,
    detections: list[dict],
    gps_data: dict,
    filename: str,
    source: str,
    image_metadata: Optional[dict],
    original_bytes: bytes,
    annotated_image: Optional[np.ndarray],
    is_offline: bool,
) -> bool:
    """Persist authenticated Cloudinary image references and a Firestore record.

    Firestore availability is checked before any image upload. No local database
    or alternate Firebase record store is written when persistence fails.
    """
    del is_offline  # Offline detections are not queued locally by design.
    try:
        fs = _require_firestore()
    except DetectionStoreUnavailableError:
        raise DetectionPersistenceError("Firestore is unavailable for new image records") from None
    document = _record_ref(fs, user_id, record_id)
    if document is None:
        raise DetectionPersistenceError("Detection identity is invalid")

    detection_record = _serialize_detection_payload(
        record_id=record_id,
        user_id=user_id,
        email=email,
        detections=detections,
        gps_data=gps_data,
        filename=filename,
        source=source,
        image_metadata=image_metadata,
    )
    try:
        image_fields = save_upload_image_assets(record_id, original_bytes, annotated_image)
    except CloudinaryStorageError:
        raise DetectionPersistenceError("Cloudinary image upload failed") from None
    detection_record.update(image_fields)

    try:
        document.create(detection_record)
    except Exception as write_error:
        # Firestore can commit a write while the client observes a timeout.
        # Keep assets until a read of the exact document proves they are unused.
        try:
            snapshot = document.get()
            existing = snapshot.to_dict() if snapshot.exists else None
        except Exception:
            logger.warning("Firestore write outcome is uncertain for detection %s", record_id)
            raise DetectionPersistenceError(
                "Detection persistence outcome is uncertain; uploaded assets were retained"
            ) from None

        if isinstance(existing, dict) and existing.get("image_assets") == image_fields["image_assets"]:
            logger.info("Firestore confirmed detection %s after an ambiguous write", record_id)
        else:
            for reference in image_fields["image_assets"].values():
                delete_asset(reference)
            logger.warning("Firestore did not commit detection %s (%s)", record_id, type(write_error).__name__)
            raise DetectionPersistenceError("Detection record could not be saved to Firestore") from None

    invalidate_expert_records_cache()
    return True
