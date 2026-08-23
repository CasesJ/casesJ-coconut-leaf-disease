"""
app/domain/detection.py — Pure detection record business logic.

All functions here are side-effect-free: no database calls, no HTTP.
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.core.config import (
    ANNOTATED_IMAGE_DIR,
    PENDING_VERIFICATION_STATUS,
    UPLOAD_IMAGE_DIR,
    VERIFIED_STATUS,
)


def _build_record_signature(record: dict) -> str:
    """Create a stable signature so the same upload is not counted twice across backends."""
    try:
        detections = record.get("detections") or record.get("inference_results") or []
        if isinstance(detections, str):
            try:
                detections = json.loads(detections)
            except Exception:
                detections = []
        if not isinstance(detections, list):
            detections = []
        normalized_detections = []
        for detection in detections:
            if isinstance(detection, dict):
                normalized_detections.append(
                    {
                        "class": str(detection.get("class", "")).lower(),
                        "confidence": round(float(detection.get("confidence", 0) or 0), 4),
                    }
                )
        normalized_detections.sort(key=lambda item: (item["class"], item["confidence"]))

        gps_data = record.get("gps_data") or {}
        if isinstance(gps_data, str):
            try:
                gps_data = json.loads(gps_data)
            except Exception:
                gps_data = {}
        if isinstance(gps_data, dict):
            lat = gps_data.get("latitude")
            lng = gps_data.get("longitude")
        else:
            lat = record.get("lat")
            lng = record.get("lng")

        signature_payload = {
            "user_id": str(record.get("user_id") or ""),
            "filename": str(record.get("filename") or record.get("image_path") or ""),
            "lat": round(float(lat), 6) if lat is not None else None,
            "lng": round(float(lng), 6) if lng is not None else None,
            "detections": normalized_detections,
            "source": str(record.get("source") or "upload"),
        }
        payload = json.dumps(signature_payload, sort_keys=True, separators=(",", ":"))
        return hashlib.md5(payload.encode("utf-8")).hexdigest()
    except Exception:
        return json.dumps(record, sort_keys=True, default=str)


def deduplicate_records(records: list[dict]) -> list[dict]:
    """Remove duplicate upload records that appear in multiple storage backends."""
    seen: set[str] = set()
    deduped: list[dict] = []
    for record in records or []:
        signature = _build_record_signature(record)
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(record)
    return deduped


def normalize_verification_status(record: dict) -> str:
    """Normalise verification state for records returned to the client."""
    status_value = str(record.get("verification_status") or "").strip().lower()
    if status_value in {PENDING_VERIFICATION_STATUS, VERIFIED_STATUS}:
        return status_value
    source = str(record.get("source") or "upload").lower()
    if source == "upload":
        return PENDING_VERIFICATION_STATUS
    return VERIFIED_STATUS


def apply_record_defaults(record: dict) -> dict:
    """Fill in missing fields expected by the frontend."""
    if not isinstance(record, dict):
        return record

    if not record.get("id"):
        record["id"] = (
            record.get("key")
            or record.get("record_id")
            or record.get("image_path")
            or str(uuid.uuid4())
        )

    if not record.get("filename") and record.get("image_path"):
        record["filename"] = record["image_path"]

    record_id = str(record.get("id") or "").strip()
    if record_id:
        original_image = UPLOAD_IMAGE_DIR / f"{record_id}.jpg"
        annotated_image = ANNOTATED_IMAGE_DIR / f"{record_id}.jpg"
        if original_image.exists():
            record["image_url"] = f"/static/uploads/{record_id}.jpg"
        if annotated_image.exists():
            record["annotated_image_url"] = f"/static/annotated_uploads/{record_id}.jpg"
        if not record.get("image_url") and record.get("annotated_image_url"):
            record["image_url"] = record["annotated_image_url"]

    if not record.get("detections") and record.get("inference_results"):
        inf_results = record["inference_results"]
        if isinstance(inf_results, str):
            try:
                record["detections"] = json.loads(inf_results)
            except Exception:
                record["detections"] = []
        elif isinstance(inf_results, list):
            record["detections"] = inf_results

    if not isinstance(record.get("detections"), list):
        record["detections"] = []

    if isinstance(record.get("gps_data"), dict):
        record["lat"] = record["gps_data"].get("latitude")
        record["lng"] = record["gps_data"].get("longitude")
        record["gps_source"] = record["gps_data"].get("source", "unknown")
    elif isinstance(record.get("gps_data"), str):
        try:
            gps = json.loads(record["gps_data"])
            record["lat"] = gps.get("latitude")
            record["lng"] = gps.get("longitude")
            record["gps_source"] = gps.get("source", "unknown")
        except Exception:
            pass

    if "gps_lat" in record:
        record["lat"] = record.get("lat") or record["gps_lat"]
    if "gps_lng" in record:
        record["lng"] = record.get("lng") or record["gps_lng"]

    record["verification_status"] = normalize_verification_status(record)
    record["is_verified"] = record["verification_status"] == VERIFIED_STATUS
    return record


def _extract_primary_disease(record: dict) -> tuple[str, float]:
    """Infer the primary disease and confidence from a record payload."""
    detections = record.get("detections") or record.get("inference_results") or []
    if isinstance(detections, str):
        try:
            detections = json.loads(detections)
        except Exception:
            detections = []
    if not isinstance(detections, list):
        detections = []

    if record.get("primaryDisease"):
        primary = {
            "class": str(record.get("primaryDisease") or ""),
            "confidence": float(
                record.get("primaryConfidence")
                or record.get("primary_confidence")
                or 0
            ),
        }
    elif detections:
        primary = max(
            (
                {
                    "class": str(det.get("class") or ""),
                    "confidence": float(det.get("confidence") or 0),
                }
                for det in detections
                if isinstance(det, dict)
            ),
            key=lambda item: item.get("confidence", 0),
            default={"class": "", "confidence": 0},
        )
    else:
        primary = {"class": "", "confidence": 0}

    return primary.get("class", ""), float(primary.get("confidence", 0) or 0)


def _serialize_detection_payload(
    record_id: str,
    user_id: str,
    email: str,
    detections: list[dict],
    gps_data: dict,
    filename: str,
    source: str,
    image_metadata: Optional[dict] = None,
) -> dict:
    """Build the canonical upload record used across storage backends."""
    return {
        "id": record_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "email": email,
        "user_id": user_id,
        "detections": detections,
        "count": len(detections),
        "source": source,
        "gps_data": gps_data,
        "filename": filename,
        "image_metadata": image_metadata or {},
        "image_url": f"/static/uploads/{record_id}.jpg",
        "annotated_image_url": f"/static/annotated_uploads/{record_id}.jpg",
        "verification_status": PENDING_VERIFICATION_STATUS if source == "upload" else VERIFIED_STATUS,
        "verified_by": None,
        "verified_by_uid": None,
        "verified_at": None,
    }
