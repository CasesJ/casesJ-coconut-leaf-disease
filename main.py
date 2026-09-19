import base64
import os
import numpy as np
import logging
import uuid
from typing import Any, Optional
from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect, Depends, HTTPException, status, Form, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pydantic import BaseModel, ConfigDict
from datetime import datetime, timezone
import asyncio
import threading
import time
from collections import deque
import json
import hashlib
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import cv2 after other imports to avoid async/pickling issues
import cv2
from cv2 import imdecode, imencode, IMREAD_COLOR

from model import detector
from firebase_config import verify_token, ensure_user_account
from app.core.config import CORS_ALLOWED_ORIGINS
from firebase_admin import db
try:
    from firebase_admin import firestore
    fs = firestore.client()
    FIRESTORE_AVAILABLE = True
except:
    FIRESTORE_AVAILABLE = False
    fs = None
    
from drone_gps import init_drone_gps, get_drone_gps, get_current_drone_position
from hybrid_storage.local_storage import get_local_storage, DetectionRecord

# CSV Export functionality
import csv
from io import BytesIO, StringIO
from fastapi.responses import StreamingResponse
from collections import defaultdict
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Flowable, Image, KeepTogether
from reportlab.pdfgen.canvas import Canvas
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Show potentially useful leaf-disease detections from the current YOLO26 v6
# model.  The UI always shows their confidence, so users can review lower-score
# detections instead of receiving an unexplained empty result.
UPLOAD_DISPLAY_CONFIDENCE_THRESHOLD = 0.05
UPLOAD_RECORD_CONFIDENCE_THRESHOLD = 0.05
AUTO_VERIFICATION_CONFIDENCE_THRESHOLD = 0.50
EXPERT_RECOMMENDATIONS_PATH = Path("expert_recommendations.json")
EXPERT_AUDIT_LOG_PATH = Path("expert_audit_log.jsonl")
USER_NOTIFICATIONS_PATH = Path("user_notifications.json")
UPLOAD_IMAGE_DIR = Path("static/uploads")
ANNOTATED_IMAGE_DIR = Path("static/annotated_uploads")
EXPERT_ACCOUNT_EMAIL = os.environ["EXPERT_ACCOUNT_EMAIL"].strip().lower()
EXPERT_ACCOUNT_PASSWORD = os.environ["EXPERT_ACCOUNT_PASSWORD"]
EXPERT_ACCOUNT_UID = os.getenv("EXPERT_ACCOUNT_UID", "").strip()
EXPERT_ACCOUNT_EMAILS = {
    email.strip().lower()
    for email in os.getenv("EXPERT_ACCOUNT_EMAILS", "").split(",")
    if email.strip()
}
EXPERT_ACCOUNT_EMAILS.add(EXPERT_ACCOUNT_EMAIL)
EXPERT_ACCOUNT_UIDS = {
    uid.strip()
    for uid in os.getenv("EXPERT_ACCOUNT_UIDS", "").split(",")
    if uid.strip()
}
PENDING_VERIFICATION_STATUS = "pending_verification"
VERIFIED_STATUS = "verified"
EXPERT_DISEASE_OPTIONS = [
    "Caterpillars",
    "Cercospora",
    "Drying of Leaflets",
    "Healthy",
    "Pestalotiopsis",
    "Bud Root",
]

UPLOAD_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
ANNOTATED_IMAGE_DIR.mkdir(parents=True, exist_ok=True)


def _build_record_signature(record: dict) -> str:
    """Create a stable signature so the same upload is not counted twice across storage backends."""
    try:
        # ✅ FIXED: Handle both 'detections' and 'inference_results' field names
        detections = record.get('detections') or record.get('inference_results') or []
        if isinstance(detections, str):
            try:
                detections = json.loads(detections)
            except:
                detections = []
        if not isinstance(detections, list):
            detections = []
        normalized_detections = []
        for detection in detections:
            if isinstance(detection, dict):
                normalized_detections.append({
                    'class': str(detection.get('class', '')).lower(),
                    'confidence': round(float(detection.get('confidence', 0) or 0), 4)
                })
        normalized_detections.sort(key=lambda item: (item['class'], item['confidence']))

        gps_data = record.get('gps_data') or {}
        if isinstance(gps_data, str):
            try:
                gps_data = json.loads(gps_data)
            except:
                gps_data = {}
        if isinstance(gps_data, dict):
            lat = gps_data.get('latitude')
            lng = gps_data.get('longitude')
        else:
            lat = record.get('lat')
            lng = record.get('lng')

        signature_payload = {
            'user_id': str(record.get('user_id') or ''),
            'filename': str(record.get('filename') or record.get('image_path') or ''),  # ✅ Check both filename and image_path
            'lat': round(float(lat), 6) if lat is not None else None,
            'lng': round(float(lng), 6) if lng is not None else None,
            'detections': normalized_detections,
            'source': str(record.get('source') or 'upload')
        }
        payload = json.dumps(signature_payload, sort_keys=True, separators=(',', ':'))
        return hashlib.md5(payload.encode('utf-8')).hexdigest()
    except Exception:
        return json.dumps(record, sort_keys=True, default=str)


def deduplicate_records(records: list[dict]) -> list[dict]:
    """Remove duplicate upload records that appear in multiple storage backends."""
    seen = set()
    deduped = []
    for record in records or []:
        signature = _build_record_signature(record)
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(record)
    return deduped


def normalize_verification_status(record: dict) -> str:
    """Normalize verification state for records returned to the client."""
    status_value = str(record.get("verification_status") or "").strip().lower()
    source = str(record.get("source") or "upload").lower()

    if source == "upload" and upload_verification_status(
        record.get("detections") or record.get("inference_results") or [], source
    ) == VERIFIED_STATUS:
        return VERIFIED_STATUS

    if status_value in {PENDING_VERIFICATION_STATUS, VERIFIED_STATUS}:
        return status_value

    if source == "upload":
        return PENDING_VERIFICATION_STATUS
    return VERIFIED_STATUS


def upload_verification_status(detections, source: str = "upload") -> str:
    """Automatically verify uploaded records whose primary confidence exceeds 50%."""
    if str(source or "").lower() != "upload":
        return VERIFIED_STATUS
    if isinstance(detections, str):
        try:
            detections = json.loads(detections)
        except (TypeError, ValueError):
            detections = []
    if not isinstance(detections, list):
        detections = []
    primary_confidence = max(
        (
            float(detection.get("confidence") or 0)
            for detection in detections
            if isinstance(detection, dict)
        ),
        default=0.0,
    )
    return VERIFIED_STATUS if primary_confidence > AUTO_VERIFICATION_CONFIDENCE_THRESHOLD else PENDING_VERIFICATION_STATUS


def is_expert_identity(decoded: dict | None) -> bool:
    """Check whether the current Firebase identity should have expert access."""
    if not decoded:
        return False

    role = str(decoded.get("role") or decoded.get("custom_role") or "").strip().lower()
    if role == "expert":
        return True

    email = str(decoded.get("email") or "").strip().lower()
    uid = str(decoded.get("uid") or "").strip()
    if EXPERT_ACCOUNT_EMAIL and email == EXPERT_ACCOUNT_EMAIL:
        return True
    if EXPERT_ACCOUNT_UID and uid == EXPERT_ACCOUNT_UID:
        return True
    if email and email in EXPERT_ACCOUNT_EMAILS:
        return True
    if uid and uid in EXPERT_ACCOUNT_UIDS:
        return True
    return False


def load_expert_recommendations() -> dict:
    """Load expert-edited recommendation overrides from disk."""
    if not EXPERT_RECOMMENDATIONS_PATH.exists():
        return {}

    try:
        with open(EXPERT_RECOMMENDATIONS_PATH, "r", encoding="utf-8") as file:
            data = json.load(file)
            return data if isinstance(data, dict) else {}
    except Exception as error:
        logger.warning(f"Could not load expert recommendations: {error}")
        return {}


def save_expert_recommendations(data: dict) -> None:
    """Persist expert-edited recommendation overrides."""
    with open(EXPERT_RECOMMENDATIONS_PATH, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=True)


def append_expert_audit_event(action: str, actor: dict, target: dict | None = None, details: dict | None = None) -> None:
    """Append an expert action to the audit log."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "actor_email": actor.get("email") if actor else None,
        "actor_uid": actor.get("uid") if actor else None,
        "target": target or {},
        "details": details or {},
    }
    try:
        with open(EXPERT_AUDIT_LOG_PATH, "a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=True) + "\n")
    except Exception as error:
        logger.warning(f"Could not write expert audit entry: {error}")


def create_user_notification(user_id: str, record_id: str, title: str, message: str) -> None:
    """Persist an in-app alert for a record owner across app sessions."""
    if not user_id:
        return
    notice = {
        "id": str(uuid.uuid4()), "record_id": record_id, "title": title,
        "message": message, "type": "verification", "read": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        db.reference(f"users/{user_id}/notifications/{notice['id']}").set(notice)
    except Exception as error:
        logger.warning(f"RTDB notification write failed: {error}")
    try:
        stored = json.loads(USER_NOTIFICATIONS_PATH.read_text(encoding="utf-8")) if USER_NOTIFICATIONS_PATH.exists() else {}
        items = stored.get(user_id, [])
        items.insert(0, notice)
        stored[user_id] = items[:100]
        USER_NOTIFICATIONS_PATH.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    except Exception as error:
        logger.warning(f"Local notification write failed: {error}")


def get_user_notifications(user_id: str) -> list[dict]:
    """Read notifications, preferring Firebase but retaining offline support."""
    try:
        remote = db.reference(f"users/{user_id}/notifications").get() or {}
        if isinstance(remote, dict) and remote:
            return sorted(remote.values(), key=lambda item: item.get("created_at", ""), reverse=True)
    except Exception as error:
        logger.warning(f"RTDB notification read failed: {error}")
    try:
        stored = json.loads(USER_NOTIFICATIONS_PATH.read_text(encoding="utf-8")) if USER_NOTIFICATIONS_PATH.exists() else {}
        return stored.get(user_id, [])
    except Exception as error:
        logger.warning(f"Local notification read failed: {error}")
        return []


def read_expert_audit_events(limit: int = 100) -> list[dict]:
    """Read the latest expert actions from the audit log."""
    if not EXPERT_AUDIT_LOG_PATH.exists():
        return []

    events: list[dict] = []
    try:
        with open(EXPERT_AUDIT_LOG_PATH, "r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except Exception:
                    continue
    except Exception as error:
        logger.warning(f"Could not read expert audit log: {error}")
        return []

    events.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
    return events[:limit]


def normalize_disease_key(disease_name: str) -> str:
    """Create a stable disease key for recommendation overrides."""
    return str(disease_name or "").strip().lower().replace(" ", "_")


def apply_expert_recommendation_override(base_recommendation: dict, disease_name: str) -> dict:
    """Merge any expert override into the recommendation payload."""
    overrides = load_expert_recommendations()
    override = overrides.get(normalize_disease_key(disease_name))
    if not override or not override.get("active", True):
        return base_recommendation

    merged = dict(base_recommendation)
    recommendation_data = dict(base_recommendation.get("recommendations", {}))
    recommendation_data["fertilizer"] = override.get("fertilizer", recommendation_data.get("fertilizer"))
    recommendation_data["treatment"] = override.get("treatment", recommendation_data.get("treatment"))
    prevention = override.get("prevention")
    if isinstance(prevention, list) and prevention:
        recommendation_data["prevention"] = prevention
    merged["recommendations"] = recommendation_data
    note = override.get("note")
    if note:
        merged["note"] = f"{merged.get('note', '')} | Expert note: {note}".strip(" |")
    merged["expert_override"] = override
    merged["source"] = "expert_override"
    return merged


def build_recommendation_response(disease_name: str, confidence: float, lat: float = None, lng: float = None, accuracy: float = None) -> dict:
    """Build the default recommendation payload for one detection.

    Expert edits are deliberately kept on the individual detection record.  A
    disease-level edit (for example, ``leaf_rot``) must not change the advice
    shown for every other leaf-rot upload.
    """
    import math

    if not disease_name or not isinstance(disease_name, str):
        raise ValueError("Invalid disease name provided")
    if not isinstance(confidence, (int, float)) or confidence is None or math.isnan(confidence) or math.isinf(confidence):
        raise ValueError("Invalid confidence value provided")

    user_location = None
    if lat is not None and lng is not None:
        user_location = {
            "lat": lat,
            "lng": lng,
            "accuracy": accuracy if accuracy else 10.0,
        }

    recommendations = detector.get_fertilizer_recommendation(
        disease_name=disease_name,
        confidence=confidence,
        gps_data=None,
        user_location=user_location,
    )

    prevention = recommendations.get("prevention", [])
    if isinstance(prevention, str):
        prevention = [prevention]

    response = {
        "disease": recommendations["disease"],
        "confidence_percent": recommendations["confidence"],
        "model_used": recommendations.get("model", "Unknown"),
        "recommendations": {
            "fertilizer": recommendations["fertilizer"],
            "treatment": recommendations["treatment"],
            "prevention": prevention,
        },
        "location": recommendations.get("location", {}),
        "note": recommendations["note"],
        "source": "default",
    }
    return response


def _find_record_for_user(user_id: str, record_id: str) -> dict | None:
    """Look up a record across local storage and Firebase backends."""
    try:
        storage = get_local_storage()
        record = storage.get_detection(record_id)
        if record and (not user_id or str(record.get("user_id") or "") == str(user_id)):
            return record
    except Exception as error:
        logger.warning(f"Local record lookup failed for {record_id}: {error}")

    try:
        ref = db.reference(f"users/{user_id}/uploads/{record_id}")
        record = ref.get()
        if isinstance(record, dict):
            record.setdefault("id", record_id)
            record.setdefault("user_id", user_id)
            return record
    except Exception as error:
        logger.warning(f"RTDB record lookup failed for {record_id}: {error}")

    if FIRESTORE_AVAILABLE:
        try:
            doc_ref = fs.collection("users").document(user_id).collection("detections").document(record_id)
            snapshot = doc_ref.get()
            if snapshot.exists:
                record = snapshot.to_dict() or {}
                record.setdefault("id", record_id)
                record.setdefault("user_id", user_id)
                return record
        except Exception as error:
            logger.warning(f"Firestore record lookup failed for {record_id}: {error}")

    return None


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

    primary = None
    if record.get("primaryDisease"):
        primary = {
            "class": str(record.get("primaryDisease") or ""),
            "confidence": float(record.get("primaryConfidence") or record.get("primary_confidence") or 0),
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


def _sync_recommendation_snapshot_for_disease(disease_name: str) -> int:
    """Update saved recommendation snapshots for all verified records of a disease."""
    disease_key = normalize_disease_key(disease_name)
    if not disease_key:
        return 0

    synced = 0
    try:
        recommendation = build_recommendation_response(disease_name=disease_name, confidence=0.85)
    except Exception as error:
        logger.warning(f"Could not build synced recommendation for {disease_name}: {error}")
        return 0

    try:
        records = _get_all_user_records(limit=5000)
    except Exception as error:
        logger.warning(f"Could not collect records for recommendation sync: {error}")
        return 0

    for record in records:
        if normalize_verification_status(record) != VERIFIED_STATUS:
            continue

        primary_disease, _confidence = _extract_primary_disease(record)
        if normalize_disease_key(primary_disease) != disease_key:
            continue

        user_id = str(record.get("user_id") or "").strip()
        record_id = str(record.get("id") or record.get("record_id") or "").strip()
        if not user_id or not record_id:
            continue

        updates = {
            "recommendation_snapshot": recommendation,
            "recommendation_source": recommendation.get("source", "default"),
        }
        if _update_record_status_everywhere(user_id, record_id, updates):
            synced += 1

    return synced


def apply_record_defaults(record: dict) -> dict:
    """Fill in missing fields expected by the frontend."""
    if not isinstance(record, dict):
        return record

    if not record.get("id"):
        record["id"] = record.get("key") or record.get("record_id") or record.get("image_path") or str(uuid.uuid4())

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


def _serialize_detection_payload(record_id: str, user_id: str, email: str, detections: list[dict], gps_data: dict, filename: str, source: str, image_metadata: Optional[dict] = None) -> dict:
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
        "verification_status": upload_verification_status(detections, source),
        "verified_by": None,
        "verified_by_uid": None,
        "verified_at": None,
    }


def _save_upload_image_assets(record_id: str, original_bytes: bytes, annotated_image: np.ndarray | None = None) -> dict:
    """Persist the exact uploaded file plus the generated annotated preview.

    The source image is deliberately written from its upload bytes, rather than
    decoded and re-encoded with OpenCV.  Re-encoding drops EXIF/XMP, including
    DJI flight and GPS metadata.
    """
    image_urls: dict[str, str] = {}
    try:
        original_path = UPLOAD_IMAGE_DIR / f"{record_id}.jpg"
        original_path.write_bytes(original_bytes)
        image_urls["image_url"] = f"/static/uploads/{record_id}.jpg"
    except Exception as error:
        logger.warning(f"Could not save original upload image for {record_id}: {error}")

    if annotated_image is not None:
        try:
            annotated_path = ANNOTATED_IMAGE_DIR / f"{record_id}.jpg"
            cv2.imwrite(str(annotated_path), annotated_image, [cv2.IMWRITE_JPEG_QUALITY, 92])
            image_urls["annotated_image_url"] = f"/static/annotated_uploads/{record_id}.jpg"
        except Exception as error:
            logger.warning(f"Could not save annotated upload image for {record_id}: {error}")

    return image_urls


def _update_record_status_everywhere(user_id: str, record_id: str, updates: dict) -> bool:
    """Update a record in every storage backend we control."""
    updated = False

    try:
        ref = db.reference(f"users/{user_id}/uploads/{record_id}")
        current = ref.get()
        if current is not None:
            current.update(updates)
            ref.set(current)
            updated = True
    except Exception as error:
        logger.warning(f"RTDB update failed for {record_id}: {error}")

    if FIRESTORE_AVAILABLE:
        try:
            doc_ref = fs.collection("users").document(user_id).collection("detections").document(record_id)
            doc_snapshot = doc_ref.get()
            if doc_snapshot.exists:
                doc_ref.set({**doc_snapshot.to_dict(), **updates})
                updated = True
        except Exception as error:
            logger.warning(f"Firestore update failed for {record_id}: {error}")

    try:
        storage = get_local_storage()
        updated = storage.update_detection_fields(record_id, **updates) or updated
    except Exception as error:
        logger.warning(f"Local storage update failed for {record_id}: {error}")

    return updated


def reconcile_all_high_confidence_uploads() -> int:
    """Migrate every stored upload above 50% to verified across all backends."""
    corrected = 0

    try:
        storage = get_local_storage()
        for record in storage.get_all_detections(limit=100000):
            if (
                normalize_verification_status(record) == VERIFIED_STATUS
                and record.get("verification_status") != VERIFIED_STATUS
                and storage.update_detection_fields(record["id"], verification_status=VERIFIED_STATUS)
            ):
                corrected += 1
    except Exception as error:
        logger.warning("Local automatic-verification migration failed: %s", error)

    try:
        users = db.reference("users").get() or {}
        for user_id, user_data in users.items():
            uploads = user_data.get("uploads") if isinstance(user_data, dict) else None
            if not isinstance(uploads, dict):
                continue
            for record_id, record in uploads.items():
                if not isinstance(record, dict):
                    continue
                record.setdefault("source", "upload")
                if (
                    normalize_verification_status(record) == VERIFIED_STATUS
                    and record.get("verification_status") != VERIFIED_STATUS
                ):
                    db.reference(f"users/{user_id}/uploads/{record_id}").update(
                        {"verification_status": VERIFIED_STATUS}
                    )
                    corrected += 1
    except Exception as error:
        logger.warning("RTDB automatic-verification migration failed: %s", error)

    if FIRESTORE_AVAILABLE:
        try:
            for user_doc in fs.collection("users").stream():
                for collection_name in ("detections", "uploads"):
                    for document in user_doc.reference.collection(collection_name).stream():
                        record = document.to_dict() or {}
                        record.setdefault("source", "upload")
                        if (
                            normalize_verification_status(record) == VERIFIED_STATUS
                            and record.get("verification_status") != VERIFIED_STATUS
                        ):
                            document.reference.update({"verification_status": VERIFIED_STATUS})
                            corrected += 1
        except Exception as error:
            logger.warning("Firestore automatic-verification migration failed: %s", error)

    return corrected


def _get_all_user_records(limit: int = 2000) -> list[dict]:
    """Collect records from local storage and Firebase backends for expert review."""
    all_records: list[dict] = []

    try:
        local_storage = get_local_storage()
        all_records.extend(local_storage.get_all_detections(limit=limit))
    except Exception as error:
        logger.warning(f"Local all-record fetch failed: {error}")

    # The local database already contains every upload that the app knows about.
    # Return those records immediately so expert review stays fast and does not
    # depend on remote Firebase reads being available.
    if all_records:
        for record in all_records:
            apply_record_defaults(record)
        all_records = deduplicate_records(all_records)
        all_records.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return all_records

    try:
        users_root = db.reference("users").get() or {}
        for user_id, user_payload in users_root.items():
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
                        record["user_id"] = record.get("user_id") or user_id
                        record["source"] = record.get("source") or ("upload" if bucket_name == "uploads" else "drone")
                        record["storage_backend"] = f"rtdb_{bucket_name}"
                        all_records.append(record)
    except Exception as error:
        logger.warning(f"RTDB all-record fetch failed: {error}")

    if FIRESTORE_AVAILABLE:
        try:
            for user_doc in fs.collection("users").stream():
                for subcollection_name in ("detections", "uploads"):
                    docs = user_doc.reference.collection(subcollection_name).stream()
                    for doc in docs:
                        record = doc.to_dict() or {}
                        record["id"] = record.get("id") or doc.id
                        record["user_id"] = record.get("user_id") or user_doc.id
                        record["source"] = record.get("source") or ("upload" if subcollection_name == "uploads" else "upload")
                        record["storage_backend"] = f"firestore_{subcollection_name}"
                        all_records.append(record)
        except Exception as error:
            logger.warning(f"Firestore all-record fetch failed: {error}")

    for record in all_records:
        apply_record_defaults(record)

    all_records = deduplicate_records(all_records)
    all_records.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return all_records

# ── Local Storage Compatibility Wrappers ──
def save_detection(
    user_id: str,
    email: str,
    inference_results: dict,
    gps_data: dict = None,
    filename: str = None,
    record_id: str = None,
    verification_status: str | None = None,
) -> bool:
    """Save detection to local storage - compatibility wrapper"""
    try:
        storage = get_local_storage()
        record = DetectionRecord(
            id=record_id,
            user_id=user_id,
            email=email,
            inference_results=inference_results,
            gps_data=gps_data,
            image_path=filename or "",  # ✅ Store filename for deduplication matching
            timestamp=datetime.now(timezone.utc).isoformat(),
            verification_status=verification_status or upload_verification_status(inference_results),
        )
        storage.save_detection(record)
        return True
    except Exception as e:
        print(f"[ERROR] Error saving to local storage: {e}")
        return False

def get_user_detections(user_id: str):
    """Get user detections from local storage - compatibility wrapper"""
    try:
        storage = get_local_storage()
        return storage.get_user_detections(user_id)
    except Exception as e:
        print(f"[ERROR] Error retrieving detections: {e}")
        return []

# ── Pydantic Models ──
class SignupRequest(BaseModel):
    email: str
    password: str

class LoginRequest(BaseModel):
    token: str  # ID token from Firebase client

class UserResponse(BaseModel):
    uid: str
    email: str
    role: str = "farmer"
    is_expert: bool = False
    message: str


class ExpertRecommendationUpdate(BaseModel):
    disease: str
    fertilizer: str
    treatment: str
    prevention: list[str]
    note: str | None = None
    active: bool = True
    target_user_id: str | None = None
    target_record_id: str | None = None


class FarmerRecommendationUpdate(BaseModel):
    """A farmer's low-confidence recommendation review (not expert verification)."""
    fertilizer: str
    treatment: str
    prevention: list[str]
    note: str | None = None


class VerificationUpdate(BaseModel):
    status: str = VERIFIED_STATUS


class RecommendationRequest(BaseModel):
    disease: str
    confidence: float
    
    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "disease": "Cercospora",
                "confidence": 0.95
            }
        }
    )


# ─── Lifecycle Management ──────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown"""
    # Startup
    logger.info("=" * 70)
    logger.info("🚀 Coconut Disease Detector - Starting")
    logger.info("=" * 70)

    # Initialize drone GPS (post-flight mode - no connection needed)
    try:
        drone_gps = init_drone_gps(drone_ip="192.168.1.1", port=8889, use_simulation=False)
        connected = await drone_gps.connect()
        if connected:
            logger.info("✅ Drone GPS initialized (will extract from image EXIF or browser geolocation)")
        else:
            logger.warning("⚠️  Drone GPS connection failed (will use browser location)")
    except Exception as e:
        logger.warning(f"⚠️  Drone GPS init error: {str(e)}")

    # Ensure the configured expert account exists in Firebase Auth.
    try:
        expert_user, created_new = ensure_user_account(
            EXPERT_ACCOUNT_EMAIL,
            EXPERT_ACCOUNT_PASSWORD,
            {"role": "expert"},
        )
        logger.info(
            "✅ Expert account ready: %s (%s)",
            expert_user.email,
            "created" if created_new else "updated",
        )
    except Exception as e:
        logger.warning(f"⚠️  Could not prepare expert account: {str(e)}")

    logger.info("✅ Application startup complete")
    logger.info("=" * 70)

    async def reconcile_in_background():
        corrected_records = await asyncio.to_thread(reconcile_all_high_confidence_uploads)
        if corrected_records:
            logger.info("Automatically verified %s existing high-confidence upload(s)", corrected_records)

    reconciliation_task = asyncio.create_task(reconcile_in_background())

    yield

    reconciliation_task.cancel()

    # Shutdown
    logger.info("🛑 Shutting down...")

    # Disconnect drone GPS
    drone_gps = get_drone_gps()
    if drone_gps:
        await drone_gps.disconnect()
        logger.info("✅ Drone GPS disconnected")

    logger.info("✅ Application shutdown complete")


# Create FastAPI app with lifecycle management
app = FastAPI(
    title="Coconut Leaf Disease Detector",
    version="2.0.0",
    description="Disease detection and inventory management",
    lifespan=lifespan,
)

# ✅ Add CORS middleware to allow frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def prevent_frontend_cache(request: Request, call_next):
    response = await call_next(request)
    if request.url.path in {"/", "/static/app.js"}:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


app.mount("/static", StaticFiles(directory="static"), name="static")

# ─── Security ──────────────────────────────────────────────────────────────
security = HTTPBearer()

async def verify_firebase_token(credentials: Any = Depends(security)):
    """Verify Firebase token from Authorization header"""
    token = credentials.credentials
    try:
        decoded = verify_token(token)
        return decoded
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )

async def verify_firebase_token_optional(credentials: Any = Depends(security)) -> dict:
    """Verify Firebase token - returns None if offline or token invalid (for offline support)"""
    if not credentials or not credentials.credentials:
        return None
    try:
        decoded = verify_token(credentials.credentials)
        return decoded
    except Exception as e:
        # Return None instead of raising - allows offline mode
        print(f"[OFFLINE] Token verification failed (offline mode): {str(e)}")
        return None

async def get_token_optional() -> str:
    """Get token from header - returns None if not present (for offline support)"""
    return None

async def verify_token_optional(request) -> dict:
    """Optional token verification for offline mode"""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    try:
        token = auth_header.split(" ", 1)[1]
        decoded = verify_token(token)
        return decoded
    except Exception as e:
        # Return None instead of raising - allows offline mode
        print(f"[OFFLINE] Token verification failed: {str(e)}")
        return None

# ─── Authentication Endpoints ──────────────────────────────────────────────────
@app.post("/auth/verify-token", response_model=UserResponse)
async def verify_user_token(request: LoginRequest):
    """Verify Firebase ID token (called after Firebase login on client)"""
    try:
        print(f"\n{'='*60}")
        print(f"🔐 VERIFYING FIREBASE TOKEN")
        print(f"{'='*60}")
        print(f"   Token length: {len(request.token)}")
        
        decoded = verify_token(request.token)
        
        print(f"   ✅ Token verified successfully")
        print(f"   UID: {decoded.get('uid')}")
        print(f"   Email: {decoded.get('email')}")
        is_expert = is_expert_identity(decoded)
        role = "expert" if is_expert else "farmer"
        print(f"   Role: {role}")
        print(f"{'='*60}\n")
        
        return {
            "uid": decoded.get('uid'),
            "email": decoded.get('email'),
            "role": role,
            "is_expert": is_expert,
            "message": "Token verified successfully"
        }
    except Exception as e:
        print(f"   ❌ Token verification failed: {str(e)}")
        print(f"{'='*60}\n")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}"
        )

@app.get("/auth/user")
async def get_current_user(decoded: dict = Depends(verify_firebase_token)):
    """Get current logged-in user info"""
    is_expert = is_expert_identity(decoded)
    return {
        "uid": decoded.get('uid'),
        "email": decoded.get('email'),
        "role": "expert" if is_expert else "farmer",
        "is_expert": is_expert,
        "authenticated": True
    }

@app.post("/auth/logout")
async def logout():
    """Logout endpoint (token invalidation happens on client)"""
    return {"message": "Logout successful"}


# ─── Recommendations Endpoint ──────────────────────────────────────────────────
@app.post("/recommendations/fertilizer")
async def get_recommendations(request: RecommendationRequest, lat: float = None, lng: float = None, accuracy: float = None):
    """Get fertilizer, treatment, and prevention recommendations based on detected disease
    
    Accepts optional GPS coordinates:
    - lat, lng: Browser/device geolocation (fallback)
    - accuracy: GPS accuracy in meters
    """
    try:
        # Validate request data
        if not request.disease or not isinstance(request.disease, str):
            logger.error(f"Invalid disease parameter: {request.disease}")
            return {
                "disease": "Error",
                "confidence_percent": 0,
                "model_used": "Error",
                "recommendations": {
                    "fertilizer": "Invalid disease name",
                    "treatment": "Please provide a valid disease name",
                    "prevention": ["Ensure detection data is valid"]
                },
                "location": {},
                "note": "Error: Invalid disease parameter"
            }
        
        # Validate confidence is a valid number
        if not isinstance(request.confidence, (int, float)) or request.confidence is None:
            logger.error(f"Invalid confidence parameter: {request.confidence} (type: {type(request.confidence)})")
            return {
                "disease": request.disease,
                "confidence_percent": 0,
                "model_used": "Error",
                "recommendations": {
                    "fertilizer": "Unable to generate recommendations",
                    "treatment": "Invalid confidence value provided",
                    "prevention": ["Monitor tree health regularly"]
                },
                "location": {},
                "note": "Error: Invalid confidence value (must be a number between 0-100 or 0-1)"
            }
        
        # Check for NaN or Infinity
        import math
        if math.isnan(request.confidence) or math.isinf(request.confidence):
            logger.error(f"Invalid confidence value: {request.confidence}")
            return {
                "disease": request.disease,
                "confidence_percent": 0,
                "model_used": "Error",
                "recommendations": {
                    "fertilizer": "Unable to generate recommendations",
                    "treatment": "Confidence value is invalid (NaN or Infinity)",
                    "prevention": ["Monitor tree health regularly"]
                },
                "location": {},
                "note": "Error: Invalid confidence value"
            }
        
        return build_recommendation_response(
            disease_name=request.disease,
            confidence=request.confidence,
            lat=lat,
            lng=lng,
            accuracy=accuracy,
        )
    except Exception as e:
        logger.error(f"Recommendation error: {str(e)}", exc_info=True)
        return {
            "disease": request.disease if request else "Unknown",
            "confidence_percent": request.confidence if request else 0,
            "model_used": "Error",
            "recommendations": {
                "fertilizer": "Unable to generate recommendations",
                "treatment": "Please consult a local agricultural expert",
                "prevention": ["Monitor tree health regularly"]
            },
            "location": {},
            "note": f"Error: {str(e)}"
        }


# ─── Image Upload Endpoint ────────────────────────────────────────────────────
@app.post("/detect/image")
async def detect_image(request: Request, file: UploadFile = File(...), lat: float = Form(None), lng: float = Form(None), accuracy: float = Form(None)):
    """
    Detect disease in uploaded image (shows reviewable detections, saves if confidence >= configured upload threshold)
    
    POST-FLIGHT APPROACH:
    - If image contains EXIF GPS data, extracts it automatically
    - Otherwise uses browser-provided lat/lng coordinates as fallback
    - Includes accuracy metadata for tracking data source reliability
    - Works offline
    """
    # Try to extract and verify token from Authorization header
    decoded = None
    is_offline = False
    
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        try:
            decoded = verify_token(token)
        except Exception as e:
            print(f"[OFFLINE] Token verification failed: {str(e)}")
            decoded = None
    else:
        is_offline = True
    
    # Handle offline mode - user_id and email may be None
    user_id = decoded.get('uid') if decoded else "offline_user"
    email = decoded.get('email') if decoded else "offline@local"
    if decoded is None:
        is_offline = True
    
    print(f"\n{'='*60}")
    print(f"[DETECT] DETECTION REQUEST STARTED {'[OFFLINE]' if is_offline else '[ONLINE]'}")
    print(f"{'='*60}")
    print(f"[USER] Email: {email}")
    print(f"[USER] ID: {user_id}")
    
    # Read image data
    contents = await file.read()
    # Read DJI XMP as well as standard EXIF before OpenCV decodes the image.
    # The original bytes are never rewritten, so embedded metadata is preserved.
    metadata_reader = get_drone_gps() or init_drone_gps()
    image_metadata = metadata_reader.extract_image_metadata(contents, file.filename)
    
    # ✅ FIXED: TRY TO EXTRACT GPS FROM EXIF FIRST, WITH BROWSER FALLBACK
    drone_gps = get_drone_gps()
    exif_gps = None
    gps_source = "none"
    gps_accuracy = None
    
    # Prepare fallback browser coordinates (if provided by frontend)
    fallback_coords = None
    if lat is not None and lng is not None:
        fallback_coords = {
            "lat": lat,
            "lng": lng,
            "accuracy": accuracy if accuracy else 10.0  # Default browser accuracy if not provided
        }
    
    if drone_gps:
        exif_gps = drone_gps.extract_gps_from_image(contents, file.filename, fallback_coords)
        if exif_gps:
            lat = exif_gps.latitude
            lng = exif_gps.longitude
            gps_source = exif_gps.source
            # DJI images commonly omit GPSDOP; retain a usable documented estimate
            # rather than failing while formatting/saving a valid EXIF location.
            gps_accuracy = exif_gps.accuracy if exif_gps.accuracy is not None else 10.0
            print(f"[GPS] Source: {gps_source} | lat={lat:.6f}, lng={lng:.6f}, alt={exif_gps.altitude:.1f}m, accuracy={gps_accuracy:.1f}m")
        elif lat is not None and lng is not None:
            gps_source = "browser_geolocation"
            gps_accuracy = accuracy if accuracy else 15.0
            print(f"[GPS] No EXIF GPS - Using browser geolocation: lat={lat:.6f}, lng={lng:.6f}, accuracy={gps_accuracy:.1f}m")
        else:
            # No GPS from any source - use Davao default fallback
            print(f"[GPS] ⚠️  Location access denied or unavailable")
            lat = 7.0731  # Davao default center
            lng = 125.6123
            gps_source = "davao_default_fallback"
            gps_accuracy = 50000.0
            print(f"[GPS] FALLBACK: Using Davao region default: lat={lat:.6f}, lng={lng:.6f}")
    else:
        if lat is not None and lng is not None:
            gps_source = "browser_geolocation"
            gps_accuracy = accuracy if accuracy else 15.0
            print(f"[GPS] Using browser geolocation: lat={lat:.6f}, lng={lng:.6f}")
        else:
            # Fallback to Davao
            lat = 7.0731
            lng = 125.6123
            gps_source = "davao_default_fallback"
            gps_accuracy = 50000.0
            print(f"[GPS] FALLBACK: Using Davao region default coordinates")
    
    np_arr = np.frombuffer(contents, np.uint8)
    image = imdecode(np_arr, IMREAD_COLOR)

    result = detector.predict(image, conf=UPLOAD_DISPLAY_CONFIDENCE_THRESHOLD)
    all_detections = result["detections"]
    print(f"[DETECT] Total Detections: {len(all_detections)}")
    
    # Save reviewable upload detections. The frontend still shows the exact confidence
    # so lower-confidence leaf diseases are not silently hidden from the farmer.
    high_confidence_detections = [d for d in all_detections if d["confidence"] >= UPLOAD_RECORD_CONFIDENCE_THRESHOLD]
    print(f"[FILTER] Reviewable Upload Detections (>={UPLOAD_RECORD_CONFIDENCE_THRESHOLD:.0%}): {len(high_confidence_detections)}")

    # FAST JPEG encoding (70% quality = 3x faster)
    _, buffer = imencode(".jpg", result["image"], [cv2.IMWRITE_JPEG_QUALITY, 70])
    encoded = base64.b64encode(buffer).decode("utf-8")
    
    # Save to Firebase only high-confidence detections (skip if offline)
    record_id = None
    if high_confidence_detections:
        try:
            print(f"\n[SAVE] SAVING DETECTION RECORD")
            print(f"   User: {user_id}")
            print(f"   Email: {email}")
            print(f"   Detections: {len(high_confidence_detections)}")
            print(f"   GPS Source: {gps_source} | lat={lat:.6f}, lng={lng:.6f}")
            
            # Create GPS data dict for storage
            gps_data = {
                'latitude': lat,
                'longitude': lng,
                'accuracy': gps_accuracy,
                'source': gps_source,
                'timestamp': datetime.now().isoformat()
            }
            
            # Create detection record for Firebase
            record_id = f"upload_{user_id}_{int(datetime.now(timezone.utc).timestamp() * 1000)}_{uuid.uuid4().hex[:8]}"
            detection_record = _serialize_detection_payload(
                record_id=record_id,
                user_id=user_id,
                email=email,
                detections=high_confidence_detections,
                gps_data=gps_data,
                filename=file.filename,
                source="upload",
                image_metadata=image_metadata,
            )
            detection_record.update(_save_upload_image_assets(record_id, contents, result["image"]))
            
            saved = False
            
            # OFFLINE MODE: Skip Firebase, save to local storage only
            # Also save to local storage in online mode (hybrid approach)
            if is_offline:
                print(f"   [OFFLINE] Saving to local storage only...")
                if save_detection(user_id, email, high_confidence_detections, gps_data, file.filename):
                    print(f"[OK] SUCCESS: Saved to local storage (offline mode)")
                    saved = True
            else:
                # Try Realtime Database first
                try:
                    print(f"   [RTDB] Attempting Realtime Database save...")
                    ref = db.reference(f'users/{user_id}/uploads/{record_id}')
                    ref.set(detection_record)
                    print(f"[OK] SUCCESS: Saved to Realtime Database")
                    print(f"   Path: users/{user_id}/uploads/{record_id}")
                    
                    # Also save to local storage (hybrid)
                    try:
                        storage = get_local_storage()
                        storage.save_detection(
                            DetectionRecord(
                                id=record_id,
                                user_id=user_id,
                                email=email,
                                timestamp=detection_record["timestamp"],
                                inference_results=high_confidence_detections,
                                gps_data=gps_data,
                                image_path=file.filename,
                                is_synced=True,
                                verification_status=upload_verification_status(high_confidence_detections),
                            )
                        )
                    except:
                        pass
                    
                    saved = True
                except Exception as rtdb_error:
                    print(f"[WARN] Realtime Database failed: {type(rtdb_error).__name__}")
                    
                    # Fallback to Firestore
                    if FIRESTORE_AVAILABLE and not saved:
                        try:
                            print(f"   [FIRESTORE] Falling back to Firestore...")
                            fs.collection('users').document(user_id).collection('detections').document(record_id).set(detection_record)
                            print(f"[OK] SUCCESS: Saved to Firestore")
                            
                            # Also save to local storage (hybrid)
                            try:
                                storage = get_local_storage()
                                storage.save_detection(
                                    DetectionRecord(
                                        id=record_id,
                                        user_id=user_id,
                                        email=email,
                                        timestamp=detection_record["timestamp"],
                                        inference_results=high_confidence_detections,
                                        gps_data=gps_data,
                                        image_path=file.filename,
                                        is_synced=True,
                                        verification_status=upload_verification_status(high_confidence_detections),
                                    )
                                )
                            except:
                                pass
                            
                            saved = True
                        except Exception as firestore_error:
                            print(f"[WARN] Firestore failed: {type(firestore_error).__name__}")
                    
                    # Final fallback: Local JSON storage
                    if not saved:
                        print(f"   [LOCAL] Using local storage (records persist locally)...")
                        if save_detection(user_id, email, high_confidence_detections, gps_data, file.filename, record_id):
                            print(f"[OK] SUCCESS: Saved to local storage")
                            saved = True
                    
        except Exception as firebase_error:
            print(f"[ERROR] ERROR: {type(firebase_error).__name__}: {firebase_error}")
    else:
        print(f"[WARN] No reviewable upload detections to save (all < {UPLOAD_RECORD_CONFIDENCE_THRESHOLD:.0%})")
    
    print(f"{'='*60}\n")

    return {
        "detections": all_detections,
        "recorded_count": len(high_confidence_detections),
        "record_id": record_id,
        "total_detected": len(all_detections),
        "annotated_image_base64": encoded,
        "user_email": email,
        "message": f"{len(all_detections)} detections found ({len(high_confidence_detections)} saved - >= {UPLOAD_RECORD_CONFIDENCE_THRESHOLD:.0%} confidence)",
        "display_confidence_threshold": UPLOAD_DISPLAY_CONFIDENCE_THRESHOLD,
        "record_confidence_threshold": UPLOAD_RECORD_CONFIDENCE_THRESHOLD,
        "model": {
            "name": "YOLO26 v6",
            "backend": detector.backend,
            "weights": detector.active_model_path.name if detector.active_model_path else None,
            "classes": detector.class_names,
        },
        # ✅ Return GPS data so frontend pins disease at correct location
        "gps_lat": lat,
        "gps_lng": lng,
        "gps_accuracy": gps_accuracy,
        "gps_source": gps_source,
        "image_metadata": image_metadata,
    }


# ─── CSV Export Endpoint ──────────────────────────────────────────────────────
@app.put("/detections/my-records/{record_id}/low-confidence-recommendation")
async def save_farmer_low_confidence_recommendation(
    record_id: str,
    payload: FarmerRecommendationUpdate,
    decoded: dict = Depends(verify_firebase_token),
):
    """Save a farmer review for a <50% result while retaining expert verification."""
    user_id = str(decoded.get("uid") or "").strip()
    record = _find_record_for_user(user_id, record_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")
    disease, confidence = _extract_primary_disease(record)
    if not disease:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to determine record disease")
    if confidence >= 0.5:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only recommendations below 50% confidence can be edited by the farmer")

    snapshot = {
        "disease": disease, "confidence_percent": round(confidence * 100, 2),
        "model_used": "Farmer reviewed",
        "recommendations": {"fertilizer": payload.fertilizer, "treatment": payload.treatment, "prevention": payload.prevention},
        "note": payload.note or "", "source": "farmer_review", "recommendation_scope": "record",
    }
    updated = _update_record_status_everywhere(user_id, record_id, {
        "recommendation_snapshot": snapshot,
        "recommendation_source": "farmer_review",
        "farmer_recommendation_confirmed": True,
        "farmer_recommendation_confirmed_at": datetime.now(timezone.utc).isoformat(),
        # Farmer confirmation never replaces the expert's verification.
        "verification_status": PENDING_VERIFICATION_STATUS,
    })
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")
    return {"message": "Farmer recommendation saved and sent for expert verification", "recommendation_snapshot": snapshot}


@app.get("/api/export-csv")
async def export_detections_csv(request: Request):
    """
    Export all disease detections as CSV with inventory summary
    Works with or without authentication
    """
    try:
        # Try to get token but don't fail if missing
        auth_header = request.headers.get("Authorization", "")
        user_info = None
        
        if auth_header.startswith("Bearer "):
            try:
                token = auth_header.split(" ", 1)[1]
                user_info = verify_token(token)
                logger.info(f"📊 CSV export for user: {user_info.get('email')}")
            except Exception as e:
                logger.warning(f"⚠️  Token verification failed: {e}")
        
        logger.info("📊 CSV export request received")
        
        # Read from detection_history.json
        all_detections = []
        detection_file = Path("detection_history.json")
        
        if detection_file.exists():
            with open(detection_file, 'r') as f:
                all_detections = json.load(f)
                logger.info(f"✅ Loaded {len(all_detections)} detections from JSON")
        
        if not all_detections:
            logger.warning("⚠️  No detections found")
            csv_content = "Disease Name,Confidence,Severity,Field ID,Detection Date,Location,Email,Source\n"
        else:
            # Calculate disease statistics
            disease_stats = defaultdict(lambda: {
                'count': 0,
                'high': 0,
                'medium': 0,
                'low': 0,
                'avg_confidence': 0,
                'total_confidence': 0
            })
            
            for detection in all_detections:
                disease = detection.get('disease_name', 'Unknown')
                confidence = float(detection.get('confidence', 0))
                severity = detection.get('severity', 'low')
                
                disease_stats[disease]['count'] += 1
                disease_stats[disease]['total_confidence'] += confidence
                
                if severity == 'high':
                    disease_stats[disease]['high'] += 1
                elif severity == 'medium':
                    disease_stats[disease]['medium'] += 1
                else:
                    disease_stats[disease]['low'] += 1
            
            # Calculate average confidence
            for disease in disease_stats:
                if disease_stats[disease]['count'] > 0:
                    disease_stats[disease]['avg_confidence'] = round(
                        disease_stats[disease]['total_confidence'] / disease_stats[disease]['count'], 4
                    )
            
            # Build CSV
            csv_buffer = StringIO()
            csv_buffer.write("Disease Name,Confidence,Severity,Field ID,Detection Date,Location,Email,Source\n")
            
            # Write detections
            for detection in all_detections:
                disease = detection.get('disease_name', 'Unknown')
                confidence = float(detection.get('confidence', 0))
                severity = detection.get('severity', 'low')
                
                location_str = ""
                if detection.get('location'):
                    if isinstance(detection['location'], dict):
                        location_str = f"{detection['location'].get('lat', '')},{detection['location'].get('lng', '')}"
                
                csv_buffer.write(f"{disease},{confidence:.4f},{severity},{detection.get('field_id', '')},{detection.get('detection_date', '')},{location_str},{detection.get('email', '')},detection\n")
            
            # Summary section
            csv_buffer.write("\n=== INVENTORY SUMMARY ===\n\n")
            csv_buffer.write("Disease,Total Count,Avg Confidence,High,Medium,Low\n")
            
            total_detections = 0
            total_high = 0
            total_medium = 0
            total_low = 0
            total_confidence = 0
            
            for disease in sorted(disease_stats.keys(), key=lambda x: disease_stats[x]['count'], reverse=True):
                stats = disease_stats[disease]
                csv_buffer.write(f"{disease},{stats['count']},{stats['avg_confidence']},{stats['high']},{stats['medium']},{stats['low']}\n")
                total_detections += stats['count']
                total_high += stats['high']
                total_medium += stats['medium']
                total_low += stats['low']
                total_confidence += stats['total_confidence']
            
            csv_buffer.write(f"\nTOTAL,{total_detections},{round(total_confidence/total_detections, 4) if total_detections > 0 else 0},{total_high},{total_medium},{total_low}\n")
            
            csv_content = csv_buffer.getvalue()
            logger.info(f"✅ CSV generated: {total_detections} detections")
        
        return StreamingResponse(
            iter([csv_content]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=disease_detections.csv"}
        )
    
    except Exception as e:
        logger.error(f"❌ CSV export error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))




# ─── Get User's Detection Records ──────────────────────────────────────────────
def build_user_records_pdf(records: list[dict], email: str) -> bytes:
    """Build a compact, multi-page analytics report for one user's records."""
    buffer = BytesIO()
    report = canvas.Canvas(buffer, pagesize=A4, pageCompression=1)
    width, height = A4
    disease_stats = defaultdict(lambda: {"count": 0, "confidence": []})
    status_counts = defaultdict(int)
    source_counts = defaultdict(int)
    monthly_counts = defaultdict(int)
    total_detections = 0
    dates = []

    for record in records:
        source_counts[str(record.get("type") or record.get("source") or "Upload").title()] += 1
        status_counts[normalize_verification_status(record).replace("_", " ").title()] += 1
        if record.get("timestamp"):
            dates.append(str(record["timestamp"])[:10])
            monthly_counts[str(record["timestamp"])[:7]] += 1
        for detection in record.get("detections") or []:
            disease = str(detection.get("class") or "Unknown")
            disease_stats[disease]["count"] += 1
            disease_stats[disease]["confidence"].append(float(detection.get("confidence") or 0))
            total_detections += 1

    def header(page_number: int) -> float:
        report.setFillColor(colors.HexColor("#164d37"))
        report.rect(0, height - 58, width, 58, fill=1, stroke=0)
        report.setFillColor(colors.white)
        report.setFont("Helvetica-Bold", 17)
        report.drawString(42, height - 35, "Coconut Leaf Disease Analytics Report")
        report.setFont("Helvetica", 8)
        report.drawRightString(width - 42, height - 35, f"Page {page_number}")
        report.setFillColor(colors.HexColor("#587364"))
        report.setFont("Helvetica", 8)
        report.drawString(42, height - 75, f"Prepared for: {email or 'User'}")
        report.drawRightString(width - 42, height - 75, f"Generated: {datetime.now(timezone.utc).astimezone().strftime('%d %b %Y, %I:%M %p')}")
        return height - 105

    def section(title: str, y: float) -> float:
        report.setFillColor(colors.HexColor("#164d37"))
        report.setFont("Helvetica-Bold", 12)
        report.drawString(42, y, title)
        return y - 18

    def text_line(label: str, value: str, y: float) -> float:
        report.setFillColor(colors.HexColor("#1d2d25"))
        report.setFont("Helvetica-Bold", 9)
        report.drawString(48, y, label)
        report.setFont("Helvetica", 9)
        report.drawString(190, y, value[:78])
        return y - 16

    page = 1
    y = header(page)
    most_common = max(disease_stats, key=lambda key: disease_stats[key]["count"], default="No disease detected")
    y = section("Summary", y)
    y = text_line("Detection records", str(len(records)), y)
    y = text_line("Total detections", str(total_detections), y)
    y = text_line("Most common disease", most_common, y)
    y = text_line("Date range", f"{min(dates)} to {max(dates)}" if dates else "No dated records", y)
    y -= 8
    y = section("Disease distribution", y)
    report.setFillColor(colors.HexColor("#e8f3ec"))
    report.rect(42, y - 14, width - 84, 18, fill=1, stroke=0)
    report.setFillColor(colors.HexColor("#164d37"))
    report.setFont("Helvetica-Bold", 8)
    report.drawString(48, y - 2, "Disease")
    report.drawString(270, y - 2, "Detections")
    report.drawString(380, y - 2, "Average confidence")
    y -= 30
    for disease, data in sorted(disease_stats.items(), key=lambda item: item[1]["count"], reverse=True):
        average = (sum(data["confidence"]) / len(data["confidence"]) * 100) if data["confidence"] else 0
        report.setFillColor(colors.HexColor("#1d2d25"))
        report.setFont("Helvetica", 9)
        report.drawString(48, y, disease[:34])
        report.drawString(282, y, str(data["count"]))
        report.drawString(398, y, f"{average:.1f}%")
        y -= 16
    if not disease_stats:
        report.setFont("Helvetica", 9)
        report.drawString(48, y, "No detections recorded")
        y -= 16
    y -= 8
    y = section("Record analytics", y)
    y = text_line("Record sources", ", ".join(f"{name}: {count}" for name, count in sorted(source_counts.items())) or "-", y)
    y = text_line("Verification status", ", ".join(f"{name}: {count}" for name, count in sorted(status_counts.items())) or "-", y)
    y = text_line("Monthly record trend", ", ".join(f"{month}: {count}" for month, count in sorted(monthly_counts.items())) or "No dated records", y)

    page += 1
    report.showPage()
    y = header(page)
    y = section("Detection record details", y)
    report.setFillColor(colors.HexColor("#e8f3ec"))
    report.rect(42, y - 14, width - 84, 18, fill=1, stroke=0)
    report.setFillColor(colors.HexColor("#164d37"))
    report.setFont("Helvetica-Bold", 8)
    report.drawString(48, y - 2, "Date")
    report.drawString(126, y - 2, "Source")
    report.drawString(190, y - 2, "Detections")
    report.drawString(470, y - 2, "Status")
    y -= 30
    for record in records:
        if y < 70:
            page += 1
            report.showPage()
            y = header(page)
            y = section("Detection record details (continued)", y)
        detections = "; ".join(f"{item.get('class', 'Unknown')} ({float(item.get('confidence') or 0) * 100:.1f}%)" for item in record.get("detections") or []) or "No detections"
        report.setFillColor(colors.HexColor("#1d2d25"))
        report.setFont("Helvetica", 7.5)
        report.drawString(48, y, str(record.get("timestamp") or "-")[:16].replace("T", " "))
        report.drawString(126, y, str(record.get("type") or record.get("source") or "Upload")[:10])
        report.drawString(190, y, detections[:48])
        report.drawString(470, y, normalize_verification_status(record).replace("_", " ").title()[:14])
        y -= 15
    report.save()
    return buffer.getvalue()


class _NumberedReportCanvas(Canvas):
    """Two-pass canvas for Page X of Y footers."""
    def __init__(self, *args, **kwargs):
        Canvas.__init__(self, *args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.setStrokeColor(colors.HexColor("#E2E8F0"))
            self.line(16 * mm, 14 * mm, A4[0] - 16 * mm, 14 * mm)
            self.setFillColor(colors.HexColor("#64748B"))
            self.setFont("Helvetica", 7.5)
            self.drawString(16 * mm, 9 * mm, "Confidential - Coconut Leaf Disease Analytics")
            self.drawRightString(A4[0] - 16 * mm, 9 * mm, f"Generated {datetime.now(timezone.utc).astimezone().strftime('%d %b %Y %H:%M')} | Page {self._pageNumber} of {total_pages}")
            Canvas.showPage(self)
        Canvas.save(self)


class _MetricCard(Flowable):
    """A compact executive KPI card with a restrained green accent."""
    def __init__(self, value: str, label: str, note: str, width: float, height: float = 35 * mm, note_color: str = "#2F6E54"):
        Flowable.__init__(self)
        self.value, self.label, self.note = value, label, note
        self.width, self.height, self.note_color = width, height, note_color

    def wrap(self, available_width, available_height):
        return self.width, self.height

    def draw(self):
        self.canv.setFillColor(colors.white)
        self.canv.setStrokeColor(colors.HexColor("#DDE5E0"))
        self.canv.roundRect(0, 0, self.width, self.height, 3 * mm, fill=1, stroke=1)
        self.canv.setFillColor(colors.HexColor("#41966E"))
        self.canv.roundRect(0, 0, 2.6 * mm, self.height, 2 * mm, fill=1, stroke=0)
        self.canv.setFillColor(colors.HexColor("#64748B"))
        self.canv.setFont("Helvetica", 7.4)
        self.canv.drawString(6 * mm, 25 * mm, self.label.upper())
        self.canv.setFillColor(colors.HexColor("#20252B"))
        self.canv.setFont("Helvetica-Bold", 18)
        self.canv.drawString(6 * mm, 14.5 * mm, self.value[:20])
        self.canv.setFillColor(colors.HexColor(self.note_color))
        self.canv.setFont("Helvetica-Bold", 7.2)
        self.canv.drawString(6 * mm, 5.2 * mm, self.note[:30])


class _StatusBadge(Flowable):
    def __init__(self, status: str):
        Flowable.__init__(self)
        self.status = status
        self.width, self.height = 30 * mm, 8 * mm

    def wrap(self, available_width, available_height):
        return self.width, self.height

    def draw(self):
        verified = self.status.lower() == "verified"
        self.canv.setFillColor(colors.HexColor("#D1FAE5" if verified else "#FEF3C7"))
        self.canv.roundRect(0, 0, self.width, self.height, 3 * mm, fill=1, stroke=0)
        self.canv.setFillColor(colors.HexColor("#059669" if verified else "#D97706"))
        self.canv.setFont("Helvetica-Bold", 6.7)
        self.canv.drawCentredString(self.width / 2, 2.8 * mm, self.status)


def build_executive_user_records_pdf(records: list[dict], email: str) -> bytes:
    """Render the authenticated user's records as an executive-grade PDF dashboard."""
    def title_case(value: Any) -> str:
        return str(value or "-").replace("_", " ").strip().title()

    def safe(value: Any) -> str:
        return str(value or "-").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # Match the dashboard Detection Color Key, including its model-label aliases.
    detection_colors = {
        "healthy": "#22C55E",
        "caterpillars": "#F97316",
        "cercospora": "#EC4899",
        "drying of leaflets": "#2563EB",
        "leaf rot": "#2563EB",
        "pestalotiopsis": "#06B6D4",
        "bud root": "#D4D800",
        "bud rot": "#D4D800",
    }

    def detection_color(disease: str) -> str:
        return detection_colors.get(str(disease or "").replace("_", " ").strip().lower(), "#64748B")

    disease_stats = defaultdict(lambda: {"count": 0, "confidence": []})
    monthly_counts = defaultdict(int)
    monthly_disease_counts = defaultdict(lambda: defaultdict(int))
    severity_counts = defaultdict(int)
    # Keep the PDF's spatial analysis aligned with the four areas displayed on
    # the Disease Location Map in the web dashboard.
    map_bounds = {"min_lat": 7.35140, "max_lat": 7.35295, "min_lng": 125.64055, "max_lng": 125.64215}
    lat_step = (map_bounds["max_lat"] - map_bounds["min_lat"]) / 2
    lng_step = (map_bounds["max_lng"] - map_bounds["min_lng"]) / 2
    area_stats = {
        f"Area {index}": {"samples": 0, "severity": defaultdict(int)}
        for index in range(1, 5)
    }
    verified_records = 0
    top_confidences = []

    def severity_for_disease(disease: str) -> str:
        normalized_disease = disease.lower()
        if any(name in normalized_disease for name in ("lethal", "bud rot", "bud root")):
            return "Critical"
        if any(name in normalized_disease for name in ("blight", "pestalotiopsis")):
            return "Severe"
        if normalized_disease != "healthy":
            return "Moderate"
        return "Mild"

    def mapped_area(record: dict) -> Optional[str]:
        gps_data = record.get("gps_data") or record.get("gps") or {}
        if isinstance(gps_data, str):
            try:
                gps_data = json.loads(gps_data)
            except (TypeError, ValueError):
                gps_data = {}
        if not isinstance(gps_data, dict):
            gps_data = {}
        try:
            lat = float(record.get("lat") if record.get("lat") is not None else gps_data.get("latitude", gps_data.get("lat")))
            lng = float(record.get("lng") if record.get("lng") is not None else gps_data.get("longitude", gps_data.get("lng")))
        except (TypeError, ValueError):
            return None
        if not (map_bounds["min_lat"] <= lat <= map_bounds["max_lat"] and map_bounds["min_lng"] <= lng <= map_bounds["max_lng"]):
            return None
        row = min(1, int((lat - map_bounds["min_lat"]) / lat_step))
        column = min(1, int((lng - map_bounds["min_lng"]) / lng_step))
        return f"Area {row * 2 + column + 1}"

    for record in records:
        timestamp = str(record.get("timestamp") or "")
        if timestamp:
            monthly_counts[timestamp[:7]] += 1
        status = title_case(normalize_verification_status(record))
        verified_records += int(status == "Verified")
        detections = record.get("detections") or []
        record_severity = "Mild"
        if detections:
            top_confidences.append(max(float(item.get("confidence") or 0) for item in detections))
        for detection in detections:
            disease = title_case(detection.get("class") or detection.get("disease") or "Unknown")
            disease_stats[disease]["count"] += 1
            disease_stats[disease]["confidence"].append(float(detection.get("confidence") or 0))
            if timestamp:
                monthly_disease_counts[timestamp[:7]][disease] += 1
            severity = severity_for_disease(disease)
            severity_counts[severity] += 1
            if ("Mild", "Moderate", "Severe", "Critical").index(severity) > ("Mild", "Moderate", "Severe", "Critical").index(record_severity):
                record_severity = severity
        area_name = mapped_area(record)
        if area_name:
            area_stats[area_name]["samples"] += 1
            area_stats[area_name]["severity"][record_severity] += 1

    primary_disease = max(disease_stats, key=lambda item: disease_stats[item]["count"], default="No Detection")
    average_top_confidence = (sum(top_confidences) / len(top_confidences) * 100) if top_confidences else 0
    total_detections = sum(values["count"] for values in disease_stats.values())
    healthy_detections = disease_stats.get("Healthy", {}).get("count", 0)
    disease_incidence = ((total_detections - healthy_detections) / total_detections * 100) if total_detections else 0
    generated_at = datetime.now(timezone.utc).astimezone()
    buffer = BytesIO()
    page_width, page_height = A4
    styles = getSampleStyleSheet()
    normal = ParagraphStyle("ExecNormal", parent=styles["Normal"], fontName="Helvetica", fontSize=8.8, leading=12.2, textColor=colors.HexColor("#293138"))
    small = ParagraphStyle("ExecSmall", parent=normal, fontSize=7.3, leading=9)
    table_header = ParagraphStyle("ExecTableHeader", parent=small, fontName="Helvetica-Bold", textColor=colors.white)
    heading = ParagraphStyle("ExecHeading", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=13.5, leading=16, textColor=colors.HexColor("#1D4938"), spaceBefore=3, spaceAfter=4)
    insight = ParagraphStyle("ExecInsight", parent=normal, fontName="Helvetica-BoldOblique", fontSize=8.4, leading=10.3, textColor=colors.HexColor("#1D4938"), leftIndent=5 * mm, rightIndent=5 * mm, spaceBefore=3 * mm, spaceAfter=3 * mm)

    def chart_image(kind: str) -> BytesIO:
        # The map-area chart is a wide horizontal chart.  Give it a matching
        # canvas rather than stretching a square image across the PDF page.
        chart_size = (7.0, 2.45) if kind == "severity" else (7.0, 3.45)
        figure, axis = plt.subplots(figsize=chart_size, dpi=170)
        figure.patch.set_facecolor("#FFFFFF")
        if kind == "trend":
            months = sorted(monthly_disease_counts) or ["No data"]
            labels = [datetime.strptime(month, "%Y-%m").strftime("%b %Y") if month != "No data" else month for month in months]
            leading = sorted(disease_stats, key=lambda item: disease_stats[item]["count"], reverse=True)[:3] or ["No detections"]
            # A line suggests a continuous trend, but most farmer reports only
            # have one or a few months of records.  Grouped bars show the
            # useful answer directly: how many samples showed each condition.
            positions = list(range(len(months)))
            bar_width = min(0.24, 0.72 / max(1, len(leading)))
            offset_start = -bar_width * (len(leading) - 1) / 2
            maximum = 0
            for index, disease in enumerate(leading):
                values = [monthly_disease_counts[month].get(disease, 0) for month in months]
                maximum = max(maximum, max(values, default=0))
                bars = axis.bar(
                    [position + offset_start + index * bar_width for position in positions], values,
                    width=bar_width, color=detection_color(disease), label=disease,
                    edgecolor="#FFFFFF", linewidth=0.8,
                )
                for bar, value in zip(bars, values):
                    if value:
                        axis.text(bar.get_x() + bar.get_width() / 2, value + max(0.12, maximum * 0.018),
                                  str(value), ha="center", va="bottom", fontsize=7.5,
                                  fontweight="bold", color="#293138")
            axis.set_title("How many samples showed each condition", loc="left", fontsize=10,
                           fontweight="bold", color="#1D4938", pad=20)
            axis.set_xticks(positions, labels)
            axis.set_ylabel("Number of classified findings", fontsize=8, color="#64748B")
            axis.set_ylim(0, max(1, maximum * 1.25))
            axis.legend(loc="upper left", frameon=False, ncol=min(3, len(leading)), fontsize=7.3,
                        bbox_to_anchor=(0, 1.14))
        elif kind == "severity":
            labels = ["Mild", "Moderate", "Severe", "Critical"]
            area_names = list(area_stats)
            left = [0] * len(area_names)
            for label, color in zip(labels, ["#77C89D", "#EEA634", "#C14B20", "#8E2025"]):
                values = [area_stats[name]["severity"][label] for name in area_names]
                axis.barh(area_names[::-1], values[::-1], left=left[::-1], color=color, label=label, edgecolor="white", height=0.56)
                left = [current + value for current, value in zip(left, values)]
            axis.set_xlabel("GPS-tagged samples", fontsize=8, color="#64748B", labelpad=5)
            # The report section already supplies the title.  Keeping only a
            # compact legend above the plot prevents label collisions.
            axis.legend(loc="lower center", frameon=False, ncol=4, fontsize=7.2,
                        bbox_to_anchor=(0.5, 1.03), columnspacing=1.4, handlelength=1.5)
            axis.margins(y=0.18)
        else:
            ordered = sorted(disease_stats, key=lambda item: disease_stats[item]["count"], reverse=True) or ["No detections"]
            values = [disease_stats[item]["count"] for item in ordered] if disease_stats else [0]
            bars = axis.barh(ordered[::-1], values[::-1], color=[detection_color(item) for item in ordered[::-1]])
            axis.set_title("Disease Distribution Across Classified Findings", loc="left", fontsize=10, fontweight="bold", color="#1D4938", pad=12)
            axis.set_xlabel("Number of classified findings", fontsize=8, color="#64748B")
            maximum = max(values) if values else 0
            axis.set_xlim(0, max(1, maximum * 1.22))
            for bar, value in zip(bars, values[::-1]):
                axis.text(bar.get_width() + max(0.2, maximum * 0.018), bar.get_y() + bar.get_height() / 2, str(value), va="center", fontsize=7.8, fontweight="bold", color="#293138")
        axis.spines[["top", "right", "left"]].set_visible(False)
        axis.grid(axis="y" if kind == "trend" else "x", color="#E2E8F0", linewidth=0.7)
        axis.tick_params(labelsize=7, colors="#64748B")
        # Keep axes, labels, and legends comfortably separated inside the chart frame.
        figure.tight_layout(pad=1.7)
        image_buffer = BytesIO()
        figure.savefig(image_buffer, format="png", transparent=False)
        plt.close(figure)
        image_buffer.seek(0)
        return image_buffer

    def first_page(canvas_obj, doc):
        canvas_obj.saveState()
        canvas_obj.setFillColor(colors.HexColor("#194936"))
        canvas_obj.rect(0, page_height - 32 * mm, page_width, 32 * mm, fill=1, stroke=0)
        canvas_obj.setFillColor(colors.HexColor("#76C9A1"))
        canvas_obj.rect(0, page_height - 32 * mm, page_width, 1.3 * mm, fill=1, stroke=0)
        canvas_obj.setFillColor(colors.white)
        canvas_obj.setFont("Helvetica-Bold", 18.5)
        canvas_obj.drawString(16 * mm, page_height - 13 * mm, "Coconut Leaf Disease Analytics Report")
        canvas_obj.setFillColor(colors.HexColor("#D5E5DC"))
        canvas_obj.setFont("Helvetica", 9.2)
        canvas_obj.drawString(16 * mm, page_height - 20 * mm, "Plant Health Monitoring  |  Detection Summary")
        canvas_obj.setFillColor(colors.HexColor("#78CBA3"))
        canvas_obj.setFont("Helvetica-Bold", 8.3)
        canvas_obj.drawRightString(page_width - 16 * mm, page_height - 13 * mm, f"REPORT NO. CLD-{generated_at.strftime('%Y-%m')}")
        canvas_obj.setFillColor(colors.HexColor("#D5E5DC"))
        canvas_obj.setFont("Helvetica", 7.8)
        canvas_obj.drawRightString(page_width - 16 * mm, page_height - 19.2 * mm, generated_at.strftime("%B %d, %Y"))
        canvas_obj.restoreState()

    def later_pages(canvas_obj, doc):
        canvas_obj.saveState()
        canvas_obj.setFillColor(colors.HexColor("#194936"))
        canvas_obj.rect(0, page_height - 32 * mm, page_width, 32 * mm, fill=1, stroke=0)
        canvas_obj.setFillColor(colors.HexColor("#76C9A1"))
        canvas_obj.rect(0, page_height - 32 * mm, page_width, 1.3 * mm, fill=1, stroke=0)
        canvas_obj.setFillColor(colors.white)
        canvas_obj.setFont("Helvetica-Bold", 18.5)
        canvas_obj.drawString(16 * mm, page_height - 13 * mm, "Coconut Leaf Disease Analytics Report")
        page_subtitles = {
            2: "Monthly Detection Trends",
            3: "Severity by Map Area",
            4: "Disease Distribution",
        }
        canvas_obj.setFillColor(colors.HexColor("#D5E5DC"))
        canvas_obj.setFont("Helvetica", 9.2)
        canvas_obj.drawString(16 * mm, page_height - 20 * mm, page_subtitles.get(canvas_obj.getPageNumber(), "Detection Summary"))
        canvas_obj.setFillColor(colors.HexColor("#78CBA3"))
        canvas_obj.setFont("Helvetica-Bold", 8.3)
        canvas_obj.drawRightString(page_width - 16 * mm, page_height - 13 * mm, f"REPORT NO. CLD-{generated_at.strftime('%Y-%m')}")
        canvas_obj.setFillColor(colors.HexColor("#D5E5DC"))
        canvas_obj.setFont("Helvetica", 7.8)
        canvas_obj.drawRightString(page_width - 16 * mm, page_height - 19.2 * mm, generated_at.strftime("%B %d, %Y"))
        canvas_obj.restoreState()

    document = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=39 * mm, bottomMargin=22 * mm, title="Coconut Leaf Disease Analytics Report")
    card_column_width = (page_width - 32 * mm) / 4
    card_width = card_column_width - 3 * mm
    cards = Table([[_MetricCard(str(len(records)), "Verified Records", "Same records shown in dashboard analytics", card_width), _MetricCard(f"{disease_incidence:.1f}%", "Disease Incidence", f"Of {total_detections} classified findings", card_width, note_color="#C44A1D"), _MetricCard(f"{average_top_confidence:.1f}%", "Model Confidence", "Average top result", card_width), _MetricCard(f"{verified_records} / {len(records)}", "Data Checked", "Expert-reviewed records only", card_width)]], colWidths=[card_column_width] * 4)
    cards.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 1.5 * mm), ("RIGHTPADDING", (0, 0), (-1, -1), 1.5 * mm), ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))

    distribution_rows = [[Paragraph("Disease", table_header), Paragraph("Detections", table_header), Paragraph("Avg. confidence", table_header)]]
    for disease, values in sorted(disease_stats.items(), key=lambda item: item[1]["count"], reverse=True):
        average = sum(values["confidence"]) / len(values["confidence"]) * 100
        distribution_rows.append([Paragraph(safe(disease), small), Paragraph(str(values["count"]), small), Paragraph(f"{average:.1f}%", small)])
    if len(distribution_rows) == 1:
        distribution_rows.append([Paragraph("No detections", small), Paragraph("0", small), Paragraph("-", small)])
    distribution_table = Table(distribution_rows, colWidths=[45 * mm, 27 * mm, 35 * mm], rowHeights=[11 * mm] + [12 * mm] * (len(distribution_rows) - 1), repeatRows=1)
    distribution_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E293B")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]), ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#E2E8F0")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9)]))

    if total_detections:
        summary_text = (f"This report summarizes {len(records):,} expert-verified detection record{'s' if len(records) != 1 else ''} containing {total_detections:,} classified finding{'s' if total_detections != 1 else ''} for {email or 'the authenticated user'}. "
                        f"The leading detected condition is {primary_disease}, while {disease_incidence:.1f}% of classified findings indicate a disease condition. "
                        f"Average top-result confidence is {average_top_confidence:.1f}%.")
        key_text = (f"<b>Key insight:</b> {primary_disease} is the most frequently detected condition, accounting for "
                    f"{(disease_stats.get(primary_disease, {}).get('count', 0) / total_detections * 100):.1f}% of all classified findings. "
                    "Use these verified records to prioritize field inspection and treatment follow-up.")
    else:
        summary_text = (f"No expert-verified classified findings are available for {email or 'the authenticated user'} yet. "
                        "This report contains no estimated or sample results; it will update when verified records are available in the dashboard.")
        key_text = "<b>Next step:</b> Wait for expert verification or upload additional leaf images, then download a new report."
    chart_box = Table([[Image(chart_image("distribution"), width=169 * mm, height=83 * mm)]], colWidths=[175 * mm], style=[("BOX", (0, 0), (-1, -1), 0.45, colors.HexColor("#E0E7E3")), ("LEFTPADDING", (0, 0), (-1, -1), 3 * mm), ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm), ("TOPPADDING", (0, 0), (-1, -1), 3 * mm), ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm)])
    insight_box = Table([[Paragraph(key_text, insight)]], colWidths=[175 * mm], style=[("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F2F6F3")), ("BOX", (0, 0), (-1, -1), 0.3, colors.HexColor("#E1E9E4")), ("ROUNDEDCORNERS", [3 * mm])])
    story = [Paragraph("Executive Summary", heading), Spacer(1, 1 * mm), Paragraph(summary_text, normal), Spacer(1, 5 * mm), cards, Spacer(1, 18 * mm), Paragraph("Disease Distribution Across Classified Findings", heading), Spacer(1, 3 * mm), chart_box, Spacer(1, 7 * mm), insight_box]
    severity_rows = [[Paragraph("Map area", table_header), Paragraph("Mild", table_header), Paragraph("Moderate", table_header), Paragraph("Severe", table_header), Paragraph("Critical", table_header), Paragraph("Treatment priority", table_header)]]
    severity_rank = {"Mild": 0, "Moderate": 1, "Severe": 3, "Critical": 5}
    mapped_areas = []
    for area_name, values in area_stats.items():
        counts = values["severity"]
        treatment_score = sum(counts[label] * severity_rank[label] for label in severity_rank)
        diseased = values["samples"] - counts["Mild"]
        prevalence = (diseased / values["samples"] * 100) if values["samples"] else 0
        mapped_areas.append((area_name, treatment_score, prevalence, diseased, values["samples"]))
        severity_rows.append([
            Paragraph(f"<b>{area_name}</b><br/><font color='#687583'>{values['samples']} mapped sample{'s' if values['samples'] != 1 else ''}</font>", small),
            Paragraph(str(counts["Mild"]), small), Paragraph(str(counts["Moderate"]), small),
            Paragraph(str(counts["Severe"]), small), Paragraph(str(counts["Critical"]), small),
            Paragraph("No treatment cases" if not treatment_score else ("Urgent" if counts["Critical"] else "Treat / inspect"), small),
        ])
    priority_area = max(mapped_areas, key=lambda item: (item[1], item[2], item[3]), default=None)
    priority_text = ("<b>Priority treatment area:</b> No GPS-tagged samples fall within the mapped farm areas yet."
                     if not priority_area or not priority_area[1] else
                     f"<b>Priority treatment area: {priority_area[0]}</b> - {priority_area[3]} of {priority_area[4]} mapped samples show disease ({priority_area[2]:.0f}%). "
                     f"This area has the highest treatment priority based on its severe and critical findings.")
    severity_table = Table(severity_rows, colWidths=[39 * mm, 17 * mm, 22 * mm, 18 * mm, 19 * mm, 47 * mm], repeatRows=1)
    severity_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E293B")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]), ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#E2E8F0")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    trend_narrative = (f"Each bar is a count from the expert-verified records shown in the dashboard. "
                       f"{primary_disease} is the most frequent result, with disease conditions representing {disease_incidence:.1f}% of all classified findings."
                       if total_detections else
                       "No expert-verified classified findings are available yet, so no trend is shown.")
    priority_box = Table([[Paragraph(priority_text, insight)]], colWidths=[175 * mm], style=[("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF7ED")), ("BOX", (0, 0), (-1, -1), 0.3, colors.HexColor("#F5D6B3")), ("ROUNDEDCORNERS", [3 * mm])])
    story += [PageBreak(), Paragraph("Monthly Detection Trend", heading), Spacer(1, 6 * mm), Image(chart_image("trend"), width=171 * mm, height=83 * mm), Spacer(1, 7 * mm), Paragraph(trend_narrative, normal), PageBreak(), Paragraph("Severity by Map Area", heading), Spacer(1, 3 * mm), Image(chart_image("severity"), width=171 * mm, height=55 * mm), Spacer(1, 4 * mm), severity_table, Spacer(1, 5 * mm), priority_box, PageBreak(), Paragraph("Disease distribution", heading), Spacer(1, 7 * mm), distribution_table]
    document.build(story, onFirstPage=first_page, onLaterPages=later_pages, canvasmaker=_NumberedReportCanvas)
    return buffer.getvalue()


@app.get("/reports/my-records.pdf")
async def download_my_records_pdf(decoded: dict = Depends(verify_firebase_token)):
    """Return a PDF built from the same verified records shown in dashboard analytics."""
    payload = await get_user_detections_endpoint(decoded)
    # The dashboard intentionally excludes records that have not yet been
    # verified by an expert. Keep the export on the exact same dataset.
    analytics_records = [
        record for record in payload.get("records", [])
        if normalize_verification_status(record) == "verified"
    ]
    pdf_bytes = build_executive_user_records_pdf(analytics_records, payload.get("email", "User"))
    return StreamingResponse(iter([pdf_bytes]), media_type="application/pdf", headers={
        "Content-Disposition": "attachment; filename=coconut-leaf-disease-analytics-report.pdf"
    })


@app.get("/detections/my-records")
async def get_user_detections_endpoint(decoded: dict = Depends(verify_firebase_token)):
    """Get all detection records for the logged-in user"""
    user_id = decoded.get('uid')
    email = decoded.get('email')
    
    print(f"\n{'='*60}")
    print(f"📖 FETCHING DETECTION RECORDS")
    print(f"{'='*60}")
    print(f"   User: {user_id}")
    print(f"   Email: {email}")
    
    all_records = []
    
    # Try local storage first (most reliable)
    print(f"\n   [LOCAL] Checking local storage...")
    local_records = get_user_detections(user_id)
    if local_records:
        for record in local_records:
            apply_record_defaults(record)
            all_records.append(record)
        print(f"   [OK] Found {len(local_records)} records in local storage")
    
    # Try Realtime Database - get both uploads and drone_log
    try:
        print(f"\n   [RTDB] Checking Realtime Database (uploads)...")
        uploads_ref = db.reference(f'users/{user_id}/uploads')
        uploads_data = uploads_ref.get()
        
        if uploads_data:
            print(f"   [OK] Found {len(uploads_data)} upload records in RTDB")
            for key, record in uploads_data.items():
                record['id'] = key
                record['type'] = 'upload'
                if 'source' not in record:
                    record['source'] = 'upload'
                record['storage_backend'] = 'rtdb_uploads'
                apply_record_defaults(record)
                all_records.append(record)
        else:
            print(f"   [INFO] No upload records in RTDB")
    except Exception as rtdb_error:
        print(f"   [WARN] RTDB Upload Error: {type(rtdb_error).__name__}")
    
    # Also check drone_log records
    try:
        print(f"\n   [RTDB] Checking Realtime Database (drone_log)...")
        drone_ref = db.reference(f'users/{user_id}/drone_log')
        drone_data = drone_ref.get()
        
        if drone_data:
            print(f"   [OK] Found {len(drone_data)} drone records in RTDB")
            for key, record in drone_data.items():
                record['id'] = key
                record['type'] = 'drone'
                if 'source' not in record:
                    record['source'] = 'drone'
                record['storage_backend'] = 'rtdb_drone_log'
                apply_record_defaults(record)
                all_records.append(record)
        else:
            print(f"   [INFO] No drone records in RTDB")
    except Exception as drone_error:
        print(f"   [WARN] RTDB Drone Error: {type(drone_error).__name__}")
    
    # Try Firestore as last resort
    if FIRESTORE_AVAILABLE and len(all_records) < 5:  # Only check if we have few records
        try:
            print(f"\n   [FIRESTORE] Checking Firestore...")
            docs = fs.collection('users').document(user_id).collection('detections').stream()
            for doc in docs:
                record = doc.to_dict()
                record['id'] = doc.id
                record['type'] = 'upload'
                if 'source' not in record:
                    record['source'] = 'upload'
                record['storage_backend'] = 'firestore'
                apply_record_defaults(record)
                all_records.append(record)
            
            if all_records:
                print(f"   [OK] Found {len(all_records)} records in Firestore")
        except Exception as firestore_error:
            print(f"   [WARN] Firestore Error: {type(firestore_error).__name__}")

    for record in all_records:
        apply_record_defaults(record)
    
    before_dedupe = len(all_records)
    all_records = deduplicate_records(all_records)
    if len(all_records) != before_dedupe:
        print(f"   [OK] Removed {before_dedupe - len(all_records)} duplicate record(s)")

    all_records.sort(key=lambda x: x.get('timestamp', ''), reverse=True)

    print(f"\n{'='*60}")
    print(f"\n{'='*60}")
    print(f"[OK] TOTAL RECORDS RETRIEVED: {len(all_records)}")
    
    # Log GPS data availability
    gps_count = sum(1 for r in all_records if r.get('lat') or r.get('lng'))
    print(f"[OK] Records with GPS coordinates: {gps_count}/{len(all_records)}")
    
    for idx, record in enumerate(all_records[:5]):  # Log first 5 records for debugging
        lat = record.get('lat')
        lng = record.get('lng')
        gps_source = record.get('gps_source', 'none')
        print(f"     Record {idx}: Type={record.get('type')}, GPS={lat is not None and lng is not None}, Source={gps_source}")
    
    print(f"{'='*60}\n")
    
    return {
        'email': email,
        'total_records': len(all_records),
        'records': all_records
    }


# ─── Get User Detection Records (Public endpoint for frontend) ──────────────────
# In-app alerts are tied to the authenticated account, never to a caller-supplied ID.
@app.get("/notifications")
async def get_notifications(decoded: dict = Depends(verify_firebase_token)):
    user_id = str(decoded.get("uid") or "")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user identity")
    notices = get_user_notifications(user_id)
    return {"notifications": notices[:30], "unread_count": sum(1 for notice in notices if not notice.get("read"))}


@app.post("/notifications/clear")
async def clear_notifications(decoded: dict = Depends(verify_firebase_token)):
    """Clear alerts after the account holder has viewed them in the bell."""
    user_id = str(decoded.get("uid") or "")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user identity")
    try:
        db.reference(f"users/{user_id}/notifications").delete()
    except Exception as error:
        logger.warning(f"RTDB notification clear failed: {error}")
    try:
        stored = json.loads(USER_NOTIFICATIONS_PATH.read_text(encoding="utf-8")) if USER_NOTIFICATIONS_PATH.exists() else {}
        stored.pop(user_id, None)
        USER_NOTIFICATIONS_PATH.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    except Exception as error:
        logger.warning(f"Local notification clear failed: {error}")
    return {"message": "Notifications cleared"}


@app.post("/notifications/mark-read")
async def mark_notifications_read(decoded: dict = Depends(verify_firebase_token)):
    """Mark alerts as viewed while preserving the user's notification history."""
    user_id = str(decoded.get("uid") or "")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user identity")
    notices = get_user_notifications(user_id)
    for notice in notices:
        notice["read"] = True
    try:
        db.reference(f"users/{user_id}/notifications").set({notice["id"]: notice for notice in notices if notice.get("id")})
    except Exception as error:
        logger.warning(f"RTDB notification mark-read failed: {error}")
    try:
        stored = json.loads(USER_NOTIFICATIONS_PATH.read_text(encoding="utf-8")) if USER_NOTIFICATIONS_PATH.exists() else {}
        stored[user_id] = notices
        USER_NOTIFICATIONS_PATH.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    except Exception as error:
        logger.warning(f"Local notification mark-read failed: {error}")
    return {"message": "Notifications marked as read"}


@app.get("/records")
async def get_records(user_id: str = None):
    """Get detection records for a user (public endpoint, accepts user_id as query param)"""
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_id query parameter is required"
        )
    
    try:
        all_records = []
        
        # Try local storage first (most reliable)
        print(f"\n   [LOCAL] Checking local storage for user {user_id}...")
        local_records = get_user_detections(user_id)
        if local_records:
            for record in local_records:
                apply_record_defaults(record)
                all_records.append(record)
            print(f"   [OK] Found {len(local_records)} records in local storage")
        
        # Try Realtime Database - get both uploads and drone_log
        try:
            print(f"\n   [RTDB] Checking Realtime Database (uploads) for user {user_id}...")
            uploads_ref = db.reference(f'users/{user_id}/uploads')
            uploads_data = uploads_ref.get()
            
            if uploads_data:
                print(f"   [OK] Found {len(uploads_data)} upload records in RTDB")
                for key, record in uploads_data.items():
                    record['id'] = key
                    # ✅ FIXED: Preserve original 'source' field for deduplication
                    # Use 'storage_backend' for tracking which backend it came from
                    if 'source' not in record:
                        record['source'] = 'upload'  # Default source for uploads
                    record['storage_backend'] = 'rtdb_uploads'
                    apply_record_defaults(record)
                    all_records.append(record)
        except Exception as rtdb_error:
            print(f"   [WARN] RTDB Upload Error: {type(rtdb_error).__name__}")
        
        # Also check drone_log records
        try:
            print(f"\n   [RTDB] Checking Realtime Database (drone_log) for user {user_id}...")
            drone_ref = db.reference(f'users/{user_id}/drone_log')
            drone_data = drone_ref.get()
            
            if drone_data:
                print(f"   [OK] Found {len(drone_data)} drone records in RTDB")
                for key, record in drone_data.items():
                    record['id'] = key
                    # ✅ FIXED: Preserve original 'source' field for deduplication
                    if 'source' not in record:
                        record['source'] = 'drone'  # Default source for drone records
                    record['storage_backend'] = 'rtdb_drone_log'
                    apply_record_defaults(record)
                    all_records.append(record)
        except Exception as drone_error:
            print(f"   [WARN] RTDB Drone Error: {type(drone_error).__name__}")
        
        # Try Firestore as last resort
        if FIRESTORE_AVAILABLE and len(all_records) < 5:
            try:
                print(f"\n   [FIRESTORE] Checking Firestore for user {user_id}...")
                docs = fs.collection('users').document(user_id).collection('detections').stream()
                for doc in docs:
                    record = doc.to_dict()
                    record['id'] = doc.id
                    # ✅ FIXED: Preserve original 'source' field for deduplication
                    if 'source' not in record:
                        record['source'] = 'upload'  # Default source for firestore records
                    record['storage_backend'] = 'firestore'
                    apply_record_defaults(record)
                    all_records.append(record)
                
                if all_records:
                    print(f"   [OK] Found {len(all_records)} records in Firestore")
            except Exception as firestore_error:
                print(f"   [WARN] Firestore Error: {type(firestore_error).__name__}")
        
        for record in all_records:
            apply_record_defaults(record)
        
        all_records = deduplicate_records(all_records)

        # Sort by timestamp (newest first)
        all_records.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        
        for record in all_records:
            apply_record_defaults(record)
        
        print(f"\n   [OK] Total records for user {user_id}: {len(all_records)}")
        
        return all_records

    except Exception as e:
        print(f"   [ERROR] Error fetching records: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching records: {str(e)}"
        )


@app.get("/expert/records")
async def get_expert_records(status: str = "pending", decoded: dict = Depends(verify_firebase_token)):
    """Get all detection records across all farmers for expert review."""
    if not is_expert_identity(decoded):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Expert access required")

    all_records = _get_all_user_records()
    pending_count = sum(1 for record in all_records if record.get("verification_status") == PENDING_VERIFICATION_STATUS)
    verified_count = sum(1 for record in all_records if record.get("verification_status") == VERIFIED_STATUS)
    status_value = str(status or "pending").strip().lower()
    if status_value == "all":
        filtered_records = all_records
    elif status_value == "verified":
        filtered_records = [record for record in all_records if record.get("verification_status") == VERIFIED_STATUS]
    else:
        filtered_records = [record for record in all_records if record.get("verification_status") == PENDING_VERIFICATION_STATUS]
    return {
        "role": "expert",
        "email": decoded.get("email"),
        "total_records": len(all_records),
        "pending_records": pending_count,
        "verified_records": verified_count,
        "filter": status_value,
        "records": filtered_records,
    }


@app.get("/expert/pending-records")
async def get_pending_records(decoded: dict = Depends(verify_firebase_token)):
    """Get only pending records for expert verification."""
    if not is_expert_identity(decoded):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Expert access required")

    records = [
        record for record in _get_all_user_records()
        if record.get("verification_status") == PENDING_VERIFICATION_STATUS
    ]
    return {
        "role": "expert",
        "total_records": len(records),
        "records": records,
    }


@app.post("/expert/records/{user_id}/{record_id}/verify")
async def verify_expert_record(
    user_id: str,
    record_id: str,
    payload: VerificationUpdate,
    decoded: dict = Depends(verify_firebase_token),
):
    """Mark a pending upload as verified by an expert."""
    if not is_expert_identity(decoded):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Expert access required")

    desired_status = str(payload.status or VERIFIED_STATUS).strip().lower()
    if desired_status not in {PENDING_VERIFICATION_STATUS, VERIFIED_STATUS}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid status")

    record = _find_record_for_user(user_id, record_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")

    detections = record.get("detections") or record.get("inference_results") or []
    if isinstance(detections, str):
        try:
            detections = json.loads(detections)
        except Exception:
            detections = []
    if not isinstance(detections, list):
        detections = []

    primary = None
    if record.get("primaryDisease"):
        primary = {
            "class": str(record.get("primaryDisease") or ""),
            "confidence": float(record.get("primaryConfidence") or record.get("primary_confidence") or (detections[0].get("confidence") if detections and isinstance(detections[0], dict) else 0) or 0),
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
            default=None,
        )

    if not primary or not primary.get("class"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to determine record disease")

    try:
        recommendation_snapshot = build_recommendation_response(
            disease_name=primary["class"],
            confidence=primary["confidence"],
        )
    except Exception as error:
        logger.warning(f"Could not build recommendation snapshot for {record_id}: {error}")
        recommendation_snapshot = {
            "disease": primary["class"],
            "confidence_percent": round(float(primary["confidence"]) * 100, 2),
            "model_used": "Unknown",
            "recommendations": {
                "fertilizer": "",
                "treatment": "",
                "prevention": [],
            },
            "location": {},
            "note": "",
            "source": "default",
        }

    updates = {
        "verification_status": desired_status,
        "verified_by": decoded.get("email"),
        "verified_by_uid": decoded.get("uid"),
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "recommendation_snapshot": recommendation_snapshot,
        "recommendation_source": recommendation_snapshot.get("source", "default"),
    }
    if desired_status == PENDING_VERIFICATION_STATUS:
        updates["verified_by"] = None
        updates["verified_by_uid"] = None
        updates["verified_at"] = None
        updates["recommendation_snapshot"] = None
        updates["recommendation_source"] = None

    updated = _update_record_status_everywhere(user_id, record_id, updates)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")

    if desired_status == VERIFIED_STATUS:
        create_user_notification(
            user_id, record_id,
            "Upload verified by an expert",
            "Your uploaded image has been reviewed and verified. View your Detection Records for the result.",
        )

    append_expert_audit_event(
        action="verify_record",
        actor=decoded,
        target={
            "user_id": user_id,
            "record_id": record_id,
        },
        details={
            "verification_status": desired_status,
        },
    )


    return {
        "message": "Record updated successfully",
        "user_id": user_id,
        "record_id": record_id,
        "verification_status": desired_status,
    }


@app.get("/expert/recommendations")
async def get_expert_recommendations(decoded: dict = Depends(verify_firebase_token)):
    """List expert-edited recommendation overrides."""
    if not is_expert_identity(decoded):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Expert access required")

    return {
        "overrides": load_expert_recommendations(),
        "count": len(load_expert_recommendations()),
    }


@app.get("/expert/diseases")
async def get_expert_diseases(decoded: dict = Depends(verify_firebase_token)):
    """Return the supported disease list for the expert editor."""
    if not is_expert_identity(decoded):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Expert access required")

    return {
        "diseases": EXPERT_DISEASE_OPTIONS,
        "count": len(EXPERT_DISEASE_OPTIONS),
    }


@app.get("/expert/recommendations/{disease}")
async def get_expert_recommendation(disease: str, decoded: dict = Depends(verify_firebase_token)):
    """Fetch a single disease recommendation override."""
    if not is_expert_identity(decoded):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Expert access required")

    overrides = load_expert_recommendations()
    key = normalize_disease_key(disease)
    return {
        "disease": disease,
        "override": overrides.get(key),
    }


@app.put("/expert/recommendations/{disease}")
async def update_expert_recommendation(
    disease: str,
    payload: ExpertRecommendationUpdate,
    decoded: dict = Depends(verify_firebase_token),
):
    """Save an expert recommendation for exactly one uploaded record."""
    if not is_expert_identity(decoded):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Expert access required")

    target_user_id = str(payload.target_user_id or "").strip()
    target_record_id = str(payload.target_record_id or "").strip()
    if not target_user_id or not target_record_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select a pending upload before saving a recommendation",
        )

    target_record = _find_record_for_user(target_user_id, target_record_id)
    if not target_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Selected record not found")

    direct_record_synced = False
    try:
        target_primary_disease, target_primary_confidence = _extract_primary_disease(target_record)
        expert_snapshot = {
                    "disease": payload.disease or target_primary_disease or disease,
                    "confidence_percent": round(float(target_primary_confidence or 0) * 100, 2),
                    "model_used": "Expert Override",
                    "recommendations": {
                        "fertilizer": payload.fertilizer,
                        "treatment": payload.treatment,
                        "prevention": payload.prevention,
                    },
                    "location": {
                        "lat": target_record.get("lat"),
                        "lng": target_record.get("lng"),
                        "source": target_record.get("gps_source") or target_record.get("location_source") or "",
                    },
                    "note": payload.note or "",
                    "source": "expert_override",
                    "recommendation_scope": "record",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
        direct_record_synced = _update_record_status_everywhere(
            target_user_id,
            target_record_id,
            {
                "verification_status": VERIFIED_STATUS,
                "verified_by": decoded.get("email"),
                "verified_by_uid": decoded.get("uid"),
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "recommendation_snapshot": expert_snapshot,
                "recommendation_source": "expert_override",
            },
        )
    except Exception as error:
        logger.warning(f"Could not save expert recommendation for record {target_record_id}: {error}")

    if direct_record_synced:
        create_user_notification(
            target_user_id, target_record_id,
            "Upload verified by an expert",
            "Your uploaded image was verified and an expert recommendation is now available in Detection Records.",
        )

    append_expert_audit_event(
        action="update_recommendation",
        actor=decoded,
        target={
            "disease": disease,
            "user_id": payload.target_user_id,
            "record_id": payload.target_record_id,
        },
        details={
            # Saving a recommendation also verifies the selected upload. Keep
            # this explicit so the audit log can show the final record status.
            "verification_status": VERIFIED_STATUS if direct_record_synced else "pending",
            "active": payload.active,
            "prevention_count": len(payload.prevention or []),
            "synced_records": 0,
            "direct_record_synced": direct_record_synced,
        },
    )
    return {
        "message": "Recommendation saved for the selected record",
        "disease": disease,
        "synced_records": 0,
        "direct_record_synced": direct_record_synced,
    }


@app.get("/expert/audit-log")
async def get_expert_audit_log(limit: int = 100, decoded: dict = Depends(verify_firebase_token)):
    """Get the latest expert audit events."""
    if not is_expert_identity(decoded):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Expert access required")

    events = read_expert_audit_events(limit=limit)
    for event in events:
        target = event.get("target") or {}
        user_id = str(target.get("user_id") or "").strip()
        record_id = str(target.get("record_id") or "").strip()
        if not user_id or not record_id:
            continue

        record = _find_record_for_user(user_id, record_id)
        if not record:
            continue

        event["upload"] = {
            "user_id": user_id,
            "email": str(record.get("email") or ""),
            "filename": str(record.get("filename") or record.get("image_path") or ""),
            "image_url": str(record.get("image_url") or ""),
            "annotated_image_url": str(record.get("annotated_image_url") or ""),
        }

    return {
        "events": events,
        "limit": limit,
    }


# ─── WebSocket Webcam Stream ──────────────────────────────────────────────────
@app.websocket("/detect/stream")
async def detect_stream(websocket: WebSocket):
    await websocket.accept()
    # Try to verify token from query params
    token = websocket.query_params.get("token")
    user_id = None
    user_email = "anonymous"
    
    if token:
        try:
            decoded = verify_token(token)
            user_id = decoded.get('uid')
            user_email = decoded.get('email', 'unknown')
        except Exception as e:
            await websocket.send_json({"error": f"Auth failed: {str(e)}"})
            await websocket.close()
            return
    
    # ⚡ ULTRA-LOW-LATENCY SETTINGS ⚡
    frame_buffer = deque(maxlen=2)  # Keep only 2 frames max (drop old ones)
    frame_count = 0
    skip_count = 0
    firebase_queue = deque(maxlen=50)  # Queue Firebase saves, don't block stream
    last_firebase_save = time.time()
    target_fps = 24  # 24 FPS = 41ms per frame
    frame_time = 1.0 / target_fps
    latency_times = deque(maxlen=30)
    
    async def background_firebase_saver():
        """Async background task to save to Firebase without blocking stream"""
        while True:
            try:
                if firebase_queue:
                    record = firebase_queue.popleft()
                    if user_id:
                        db.reference(f'users/{user_id}/drone_log').push(record)
                await asyncio.sleep(0.1)  # Don't hammer CPU
            except Exception as e:
                print(f"Firebase background save error: {e}")
                await asyncio.sleep(0.5)
    
    # Start background saver
    saver_task = asyncio.create_task(background_firebase_saver())
    
    try:
        loop_start = time.time()
        while True:
            frame_start = time.time()
            
            try:
                # ⚡ RECEIVE with timeout to drop slow frames
                data = await asyncio.wait_for(websocket.receive_text(), timeout=0.05)
            except asyncio.TimeoutError:
                # Frame timeout = drop it (too slow from client)
                skip_count += 1
                continue
            
            frame_count += 1
            
            # ⚡ SKIP FRAMES: Only process every 2nd frame (50% reduction)
            if frame_count % 2 != 0:
                skip_count += 1
                continue
            
            try:
                # ⚡ DECODE
                img_bytes = base64.b64decode(data.split(",")[-1])
                np_arr = np.frombuffer(img_bytes, np.uint8)
                image = imdecode(np_arr, IMREAD_COLOR)
                
                if image is None:
                    continue
                
                # ⚡ DETECT (OpenVINO - super fast)
                result = detector.predict(image)
                all_detections = result["detections"]
                
                # ⚡ FILTER high confidence only
                high_confidence_detections = [d for d in all_detections if d["confidence"] >= 0.50]
                
                # ⚡ QUEUE Firebase (async, non-blocking)
                if high_confidence_detections and user_id and (time.time() - last_firebase_save) > 1.0:
                    try:
                        gps_pos = get_current_drone_position()
                        firebase_queue.append({
                            'timestamp': datetime.now().isoformat(),
                            'frame': frame_count,
                            'detections': high_confidence_detections,
                            'count': len(high_confidence_detections),
                            'gps': gps_pos
                        })
                        last_firebase_save = time.time()
                    except:
                        pass  # Don't block on GPS/Firebase errors
                
                # ⚡ ENCODE (JPEG quality 70% for speed)
                _, buffer = imencode(".jpg", result["image"], [cv2.IMWRITE_JPEG_QUALITY, 70])
                encoded = base64.b64encode(buffer).decode("utf-8")
                
                # ⚡ GET GPS (non-blocking)
                try:
                    gps_position = get_current_drone_position()
                except:
                    gps_position = None
                
                # ⚡ SEND response
                frame_end = time.time()
                latency_ms = (frame_end - frame_start) * 1000
                latency_times.append(latency_ms)
                avg_latency = sum(latency_times) / len(latency_times) if latency_times else 0
                
                await websocket.send_json({
                    "detections": all_detections,
                    "total_in_frame": len(all_detections),
                    "annotated_image": f"data:image/jpeg;base64,{encoded}",
                    "user_email": user_email,
                    "gps": gps_position,
                    "recorded": len(high_confidence_detections) > 0,
                    "latency_ms": round(latency_ms, 1),
                    "avg_latency_ms": round(avg_latency, 1),
                    "frame": frame_count,
                    "fps": round(1000 / avg_latency if avg_latency > 0 else 0, 1)
                })
                
            except Exception as frame_error:
                print(f"Frame processing error: {frame_error}")
                continue
    
    except WebSocketDisconnect:
        print(f"Client {user_email} disconnected (processed {frame_count} frames, skipped {skip_count})")
        saver_task.cancel()
    except Exception as e:
        print(f"WebSocket error: {e}")
        saver_task.cancel()



# ─── Health & Info Endpoints ──────────────────────────────────────────────────
@app.get("/health")
def health():
    return {
        "status": "ok",
        "model": "YOLO26 v6",
        "backend": detector.backend,
        "weights": detector.active_model_path.name if detector.active_model_path else None,
    }

@app.get("/drone/gps")
async def get_drone_gps_position():
    """Get current drone GPS position in real-time"""
    position = get_current_drone_position()
    if position:
        return {
            "status": "connected",
            "gps": position,
            "source": "drone"
        }
    else:
        return {
            "status": "no_connection",
            "gps": None,
            "source": "none",
            "message": "Drone GPS not available. Ensure drone is connected."
        }

@app.get("/drone/gps/history")
async def get_drone_gps_history(last_n: int = 100):
    """Get recent drone GPS history"""
    drone_gps = get_drone_gps()
    if drone_gps:
        history = drone_gps.get_position_history(last_n)
        return {
            "status": "ok",
            "count": len(history),
            "history": history
        }
    else:
        return {
            "status": "error",
            "count": 0,
            "history": []
        }

@app.get("/classes")
def get_classes():
    return {"classes": detector.class_names}

@app.get("/")
def root():
    return HTMLResponse(open("static/index.html", encoding="utf-8").read())

@app.get("/login/")
def login():
    """Serve login page (client-side Firebase auth)"""
    return root()

@app.get("/favicon.ico")
async def favicon():
    """Serve a real favicon so browsers do not keep an old cached icon."""
    favicon_svg = """<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>
  <text y='75' font-size='75'>🥥</text>
</svg>"""
    return Response(content=favicon_svg, media_type="image/svg+xml")


def _is_port_available(host: str, port: int) -> bool:
    """Check whether a TCP port is free to bind."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def _pick_server_port(preferred_port: int, host: str = "0.0.0.0", max_tries: int = 20) -> int:
    """Pick the first available port at or above the preferred one."""
    port = preferred_port
    for _ in range(max_tries):
        if _is_port_available(host, port):
            return port
        port += 1
    raise RuntimeError(f"No free port found near {preferred_port}")


if __name__ == "__main__":
    import uvicorn
    preferred_port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    server_port = _pick_server_port(preferred_port, host=host)
    print("\n" + "="*70)
    print("  [START] Starting Coconut Disease Detector API")
    print("="*70)
    print(f"  [WEB] Access the web interface at: http://localhost:{server_port}")
    print(f"  [DOCS] API documentation at: http://localhost:{server_port}/docs")
    if server_port != preferred_port:
        print(f"  [PORT] {preferred_port} was busy, using {server_port} instead")
    print("  [EXIT] Press Ctrl+C to stop the server")
    print("="*70 + "\n")
    
    uvicorn.run(
        app,
        host=host,
        port=server_port,
        log_level="info"
    )
