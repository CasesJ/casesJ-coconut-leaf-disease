"""
app/services/detection_service.py — Record persistence and retrieval orchestration.

Coordinates local SQLite storage and Firebase backends (RTDB + Firestore).
All business-logic helpers (deduplication, normalisation) come from app.domain.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from app.core.config import PENDING_VERIFICATION_STATUS
from app.domain.detection import (
    _serialize_detection_payload,
    apply_record_defaults,
    deduplicate_records,
)
from app.infrastructure.imaging.image_utils import save_upload_image_assets
from app.infrastructure.storage import DetectionRecord, get_local_storage

logger = logging.getLogger(__name__)

# Lazy import Firebase RTDB so the module loads even when Firebase is offline.
def _get_db():
    from firebase_admin import db  # noqa: PLC0415
    return db


def _get_firestore():
    """Return (fs, available) tuple — fs is None when Firestore is unavailable."""
    try:
        from firebase_admin import firestore  # noqa: PLC0415
        return firestore.client(), True
    except Exception:
        return None, False


# ── Record lookup ──────────────────────────────────────────────────────────────

def find_record_for_user(user_id: str, record_id: str) -> dict | None:
    """Look up a single record across local storage and Firebase backends."""
    try:
        storage = get_local_storage()
        record = storage.get_detection(record_id)
        if record and (not user_id or str(record.get("user_id") or "") == str(user_id)):
            return record
    except Exception as error:
        logger.warning("Local record lookup failed for %s: %s", record_id, error)

    try:
        ref = _get_db().reference(f"users/{user_id}/uploads/{record_id}")
        record = ref.get()
        if isinstance(record, dict):
            record.setdefault("id", record_id)
            record.setdefault("user_id", user_id)
            return record
    except Exception as error:
        logger.warning("RTDB record lookup failed for %s: %s", record_id, error)

    fs, available = _get_firestore()
    if available:
        try:
            doc_ref = (
                fs.collection("users")
                .document(user_id)
                .collection("detections")
                .document(record_id)
            )
            snapshot = doc_ref.get()
            if snapshot.exists:
                record = snapshot.to_dict() or {}
                record.setdefault("id", record_id)
                record.setdefault("user_id", user_id)
                return record
        except Exception as error:
            logger.warning("Firestore record lookup failed for %s: %s", record_id, error)

    return None


# ── Record update ──────────────────────────────────────────────────────────────

def update_record_everywhere(user_id: str, record_id: str, updates: dict) -> bool:
    """Apply *updates* to a record in every storage backend we control."""
    updated = False

    try:
        ref = _get_db().reference(f"users/{user_id}/uploads/{record_id}")
        current = ref.get()
        if current is not None:
            current.update(updates)
            ref.set(current)
            updated = True
    except Exception as error:
        logger.warning("RTDB update failed for %s: %s", record_id, error)

    fs, available = _get_firestore()
    if available:
        try:
            doc_ref = (
                fs.collection("users")
                .document(user_id)
                .collection("detections")
                .document(record_id)
            )
            snapshot = doc_ref.get()
            if snapshot.exists:
                doc_ref.set({**snapshot.to_dict(), **updates})
                updated = True
        except Exception as error:
            logger.warning("Firestore update failed for %s: %s", record_id, error)

    try:
        storage = get_local_storage()
        updated = storage.update_detection_fields(record_id, **updates) or updated
    except Exception as error:
        logger.warning("Local storage update failed for %s: %s", record_id, error)

    return updated


# ── Bulk record retrieval ──────────────────────────────────────────────────────

def get_all_user_records(limit: int = 2000) -> list[dict]:
    """Collect records from local storage and Firebase backends for expert review."""
    all_records: list[dict] = []

    try:
        local_storage = get_local_storage()
        all_records.extend(local_storage.get_all_detections(limit=limit))
    except Exception as error:
        logger.warning("Local all-record fetch failed: %s", error)

    # Local DB already has every upload the app knows about; skip remote reads
    # to keep expert review fast when Firebase is slow or offline.
    if all_records:
        for record in all_records:
            apply_record_defaults(record)
        all_records = deduplicate_records(all_records)
        all_records.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return all_records

    try:
        users_root = _get_db().reference("users").get() or {}
        for uid, user_payload in users_root.items():
            if not isinstance(user_payload, dict):
                continue
            for bucket_name in ("uploads", "drone_log"):
                bucket = user_payload.get(bucket_name) or {}
                if isinstance(bucket, dict):
                    for record_id, record in bucket.items():
                        if not isinstance(record, dict):
                            continue
                        record = dict(record)
                        record["id"] = record.get("id") or record_id
                        record["user_id"] = record.get("user_id") or uid
                        record["source"] = record.get("source") or (
                            "upload" if bucket_name == "uploads" else "drone"
                        )
                        record["storage_backend"] = f"rtdb_{bucket_name}"
                        all_records.append(record)
    except Exception as error:
        logger.warning("RTDB all-record fetch failed: %s", error)

    fs, available = _get_firestore()
    if available:
        try:
            for user_doc in fs.collection("users").stream():
                for subcollection_name in ("detections", "uploads"):
                    docs = user_doc.reference.collection(subcollection_name).stream()
                    for doc in docs:
                        record = doc.to_dict() or {}
                        record["id"] = record.get("id") or doc.id
                        record["user_id"] = record.get("user_id") or user_doc.id
                        record["source"] = record.get("source") or "upload"
                        record["storage_backend"] = f"firestore_{subcollection_name}"
                        all_records.append(record)
        except Exception as error:
            logger.warning("Firestore all-record fetch failed: %s", error)

    for record in all_records:
        apply_record_defaults(record)

    all_records = deduplicate_records(all_records)
    all_records.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return all_records


def get_user_records(user_id: str) -> list[dict]:
    """Get all detection records for a single user (local + RTDB + Firestore)."""
    all_records: list[dict] = []

    # Local storage — most reliable, always checked first.
    try:
        storage = get_local_storage()
        local_records = storage.get_user_detections(user_id)
        for record in local_records:
            apply_record_defaults(record)
            all_records.append(record)
    except Exception as error:
        logger.warning("Local user-record fetch failed: %s", error)

    for bucket_name, source in [("uploads", "upload"), ("drone_log", "drone")]:
        try:
            ref = _get_db().reference(f"users/{user_id}/{bucket_name}")
            data = ref.get()
            if data:
                for key, record in data.items():
                    record["id"] = key
                    record.setdefault("source", source)
                    record["storage_backend"] = f"rtdb_{bucket_name}"
                    apply_record_defaults(record)
                    all_records.append(record)
        except Exception as error:
            logger.warning("RTDB %s fetch failed for user %s: %s", bucket_name, user_id, error)

    fs, available = _get_firestore()
    if available and len(all_records) < 5:
        try:
            docs = (
                fs.collection("users")
                .document(user_id)
                .collection("detections")
                .stream()
            )
            for doc in docs:
                record = doc.to_dict()
                record["id"] = doc.id
                record.setdefault("source", "upload")
                record["storage_backend"] = "firestore"
                apply_record_defaults(record)
                all_records.append(record)
        except Exception as error:
            logger.warning("Firestore user-record fetch failed: %s", error)

    for record in all_records:
        apply_record_defaults(record)

    all_records = deduplicate_records(all_records)
    all_records.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return all_records


# ── Upload persistence ─────────────────────────────────────────────────────────

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
    """Persist a detection record and its images to all available backends."""
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
    detection_record.update(save_upload_image_assets(record_id, original_bytes, annotated_image))

    saved = False

    if is_offline:
        logger.info("[OFFLINE] Saving to local storage only...")
        saved = _save_to_local(user_id, email, record_id, detections, gps_data, filename, is_synced=False)
    else:
        # Try RTDB first.
        try:
            _get_db().reference(f"users/{user_id}/uploads/{record_id}").set(detection_record)
            logger.info("[OK] Saved to Realtime Database: users/%s/uploads/%s", user_id, record_id)
            _save_to_local(user_id, email, record_id, detections, gps_data, filename, is_synced=True)
            saved = True
        except Exception as rtdb_error:
            logger.warning("[WARN] RTDB failed: %s", type(rtdb_error).__name__)

        # Fallback to Firestore.
        fs, available = _get_firestore()
        if available and not saved:
            try:
                (
                    fs.collection("users")
                    .document(user_id)
                    .collection("detections")
                    .document(record_id)
                    .set(detection_record)
                )
                logger.info("[OK] Saved to Firestore")
                _save_to_local(user_id, email, record_id, detections, gps_data, filename, is_synced=True)
                saved = True
            except Exception as fs_error:
                logger.warning("[WARN] Firestore failed: %s", type(fs_error).__name__)

        # Final fallback: local-only.
        if not saved:
            logger.info("[LOCAL] Falling back to local storage...")
            saved = _save_to_local(user_id, email, record_id, detections, gps_data, filename, is_synced=False)

    return saved


def _save_to_local(
    user_id: str,
    email: str,
    record_id: str,
    detections: list,
    gps_data: dict,
    filename: str,
    is_synced: bool,
) -> bool:
    """Write one detection record to the local SQLite database."""
    try:
        storage = get_local_storage()
        storage.save_detection(
            DetectionRecord(
                id=record_id,
                user_id=user_id,
                email=email,
                timestamp=datetime.now(timezone.utc).isoformat(),
                inference_results=detections,
                gps_data=gps_data,
                image_path=filename or "",
                is_synced=is_synced,
                verification_status=PENDING_VERIFICATION_STATUS,
            )
        )
        return True
    except Exception as exc:
        logger.error("Local storage save failed: %s", exc)
        return False
