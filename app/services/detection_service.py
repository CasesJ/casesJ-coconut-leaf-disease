"""Firestore-backed detection record persistence and retrieval."""
from __future__ import annotations

import logging
from copy import deepcopy
from threading import Lock, RLock
from time import monotonic
from typing import Optional
from io import BytesIO
import math

import numpy as np
from PIL import Image, UnidentifiedImageError

from app.domain.detection import _serialize_detection_payload, apply_record_defaults
from app.infrastructure.imaging.image_utils import (
    DetectionImageProcessingError,
    save_upload_image_assets,
)
from app.infrastructure.cloudinary_storage import CloudinaryStorageError, delete_asset

logger = logging.getLogger(__name__)


class DetectionStoreUnavailableError(RuntimeError):
    """Firestore could not serve an authoritative detection record operation."""


class DetectionPersistenceError(RuntimeError):
    """A new image or detection could not be durably committed."""


EXPERT_RECORDS_CACHE_TTL_SECONDS = 45
_expert_records_cache: dict[str, object] = {"records": None, "limit": 0, "loaded_at": 0.0}
_expert_records_cache_lock = RLock()
_expert_records_load_lock = Lock()
_expert_records_cache_version = 0


def _image_size(image_bytes: bytes, dimensions: dict | None) -> tuple[int, int] | None:
    """Return validated source dimensions, deriving them when not supplied."""
    if isinstance(dimensions, dict):
        try:
            width, height = int(dimensions.get("width")), int(dimensions.get("height"))
            if width > 0 and height > 0:
                return width, height
        except (TypeError, ValueError, OverflowError):
            pass
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            width, height = image.size
            if width > 0 and height > 0:
                return int(width), int(height)
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    return None


def _annotated_detections(detections: list[dict], dimensions: tuple[int, int] | None) -> list[dict]:
    """Preserve model boxes and add normalized xyxy coordinates when possible."""
    normalized_detections: list[dict] = []
    width, height = dimensions or (0, 0)
    for detection in detections:
        if not isinstance(detection, dict):
            continue
        value = deepcopy(detection)
        box = value.get("bbox")
        if dimensions and isinstance(box, (list, tuple)) and len(box) == 4:
            try:
                x1, y1, x2, y2 = (float(coordinate) for coordinate in box)
                if all(math.isfinite(coordinate) for coordinate in (x1, y1, x2, y2)):
                    x1 = min(max(x1, 0.0), float(width))
                    x2 = min(max(x2, 0.0), float(width))
                    y1 = min(max(y1, 0.0), float(height))
                    y2 = min(max(y2, 0.0), float(height))
                    if x2 > x1 and y2 > y1:
                        value["bbox_normalized"] = [
                            round(x1 / width, 8), round(y1 / height, 8),
                            round(x2 / width, 8), round(y2 / height, 8),
                        ]
                        value["bbox_format"] = "xyxy"
            except (TypeError, ValueError, OverflowError):
                pass
        normalized_detections.append(value)
    return normalized_detections


def invalidate_expert_records_cache() -> None:
    """Discard the short-lived expert queue cache after a record mutation."""
    global _expert_records_cache_version
    with _expert_records_cache_lock:
        _expert_records_cache_version += 1
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


def publish_thumbnail_if_absent(
    user_id: str, record_id: str, thumbnail: dict,
) -> tuple[bool, dict | None]:
    """Atomically publish *thumbnail* without replacing an existing reference."""
    fs = _require_firestore()
    ref = _record_ref(fs, user_id, record_id)
    if ref is None:
        return False, None
    try:
        from firebase_admin import firestore  # noqa: PLC0415

        @firestore.transactional
        def commit(transaction):
            snapshot = ref.get(transaction=transaction)
            record = _record_from_snapshot(snapshot, user_id, record_id)
            if record is None:
                return False, None
            assets = record.get("image_assets") if isinstance(record.get("image_assets"), dict) else {}
            existing = assets.get("thumbnail")
            if isinstance(existing, dict) and existing:
                return False, existing
            transaction.update(ref, {"image_assets.thumbnail": thumbnail})
            return True, None

        published, existing = commit(fs.transaction())
    except DetectionStoreUnavailableError:
        raise
    except Exception as error:
        logger.warning("Firestore thumbnail publication failed (%s)", type(error).__name__)
        raise DetectionStoreUnavailableError("Firestore thumbnail publication failed") from None
    if published:
        invalidate_expert_records_cache()
    return bool(published), existing


def _prepare_listed_record(record: dict, user_id: str, record_id: str) -> dict:
    """Format an already ownership-checked Firestore record for list consumers."""
    record["id"] = record_id
    record["user_id"] = user_id
    record.setdefault("source", "upload")
    record["storage_backend"] = "firestore"
    apply_record_defaults(record)
    return record


def get_all_user_records(limit: int = 500) -> list[dict]:
    """Return the expert queue from the canonical Firestore detections group."""
    # Simultaneous dashboard/audit readers share the first completed query.
    # Mutations use the separate cache lock and can still invalidate a load.
    with _expert_records_load_lock:
        return _load_all_user_records(limit)


def _load_all_user_records(limit: int) -> list[dict]:
    safe_limit = max(1, min(int(limit), 2_000))
    now = monotonic()
    with _expert_records_cache_lock:
        version = _expert_records_cache_version
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
            records.append(_prepare_listed_record(record, owner_from_path, doc.id))
        records.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    except Exception as error:
        logger.warning("Firestore expert detection query failed (%s)", type(error).__name__)
        raise DetectionStoreUnavailableError("Firestore expert queue is unavailable") from None

    records = records[:safe_limit]
    with _expert_records_cache_lock:
        # An older snapshot must not repopulate the cache after a write.
        if version == _expert_records_cache_version:
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
            records.append(_prepare_listed_record(record, user_id, doc.id))
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
    image_dimensions: dict | None = None,
    timings: dict[str, float] | None = None,
    operation_id: str = "unknown",
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

    dimensions = _image_size(original_bytes, image_dimensions)
    stored_detections = _annotated_detections(detections, dimensions)
    detection_record = _serialize_detection_payload(
        record_id=record_id,
        user_id=user_id,
        email=email,
        detections=stored_detections,
        gps_data=gps_data,
        filename=filename,
        source=source,
        image_metadata=image_metadata,
    )
    if dimensions:
        detection_record["image_dimensions"] = {"width": dimensions[0], "height": dimensions[1]}
        detection_record["annotation_format"] = "xyxy-normalized-v1"
    try:
        # ``annotated_image`` is accepted for call compatibility; only the
        # original source bytes are persisted to Cloudinary.
        del annotated_image
        if timings is None:
            if operation_id == "unknown":
                image_fields = save_upload_image_assets(record_id, original_bytes)
            else:
                image_fields = save_upload_image_assets(
                    record_id, original_bytes, operation_id=operation_id,
                )
        else:
            image_fields = save_upload_image_assets(
                record_id, original_bytes, timings=timings, operation_id=operation_id,
            )
    except DetectionImageProcessingError as error:
        raise DetectionPersistenceError(str(error)) from None
    except CloudinaryStorageError:
        raise DetectionPersistenceError("Cloudinary image upload failed") from None
    detection_record.update(image_fields)

    firestore_started = monotonic()
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
    finally:
        if timings is not None:
            timings["firestore_write_ms"] = (monotonic() - firestore_started) * 1000

    invalidate_expert_records_cache()
    return True
