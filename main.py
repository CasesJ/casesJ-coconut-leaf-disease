import base64
import os
import numpy as np
import logging
import uuid
from typing import Any, Optional
from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect, Depends, HTTPException, status, Form, Request
from fastapi.responses import HTMLResponse
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
from io import StringIO
from fastapi.responses import StreamingResponse
from collections import defaultdict

UPLOAD_DISPLAY_CONFIDENCE_THRESHOLD = 0.20
UPLOAD_RECORD_CONFIDENCE_THRESHOLD = 0.20
EXPERT_RECOMMENDATIONS_PATH = Path("expert_recommendations.json")
EXPERT_AUDIT_LOG_PATH = Path("expert_audit_log.jsonl")
UPLOAD_IMAGE_DIR = Path("static/uploads")
ANNOTATED_IMAGE_DIR = Path("static/annotated_uploads")
EXPERT_ACCOUNT_EMAIL = os.getenv("EXPERT_ACCOUNT_EMAIL", "expert2@gmail.com").strip().lower()
EXPERT_ACCOUNT_PASSWORD = os.getenv("EXPERT_ACCOUNT_PASSWORD", "adminexpert12345")
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
    if status_value in {PENDING_VERIFICATION_STATUS, VERIFIED_STATUS}:
        return status_value

    source = str(record.get("source") or "upload").lower()
    if source == "upload":
        return PENDING_VERIFICATION_STATUS
    return VERIFIED_STATUS


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
    merged["fertilizer"] = override.get("fertilizer", merged.get("fertilizer"))
    merged["treatment"] = override.get("treatment", merged.get("treatment"))
    prevention = override.get("prevention")
    if isinstance(prevention, list) and prevention:
        merged["prevention"] = prevention
    note = override.get("note")
    if note:
        merged["note"] = f"{merged.get('note', '')} | Expert note: {note}".strip(" |")
    merged["expert_override"] = override
    merged["source"] = "expert_override"
    return merged


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


def _serialize_detection_payload(record_id: str, user_id: str, email: str, detections: list[dict], gps_data: dict, filename: str, source: str) -> dict:
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
        "image_url": f"/static/uploads/{record_id}.jpg",
        "annotated_image_url": f"/static/annotated_uploads/{record_id}.jpg",
        "verification_status": PENDING_VERIFICATION_STATUS if source == "upload" else VERIFIED_STATUS,
        "verified_by": None,
        "verified_by_uid": None,
        "verified_at": None,
    }


def _save_upload_image_assets(record_id: str, original_image: np.ndarray, annotated_image: np.ndarray | None = None) -> dict:
    """Persist the uploaded image so expert review can display it later."""
    image_urls: dict[str, str] = {}
    try:
        original_path = UPLOAD_IMAGE_DIR / f"{record_id}.jpg"
        cv2.imwrite(str(original_path), original_image, [cv2.IMWRITE_JPEG_QUALITY, 92])
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
    verification_status: str = PENDING_VERIFICATION_STATUS,
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
            verification_status=verification_status,
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

    yield

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
    allow_origins=["*"],  # Allow all origins (adjust for production)
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
        
        # Prepare GPS data for recommendation
        gps_data = None
        user_location = None
        
        if lat is not None and lng is not None:
            user_location = {
                "lat": lat,
                "lng": lng,
                "accuracy": accuracy if accuracy else 10.0
            }
        
        # Get recommendations from the detector model
        recommendations = detector.get_fertilizer_recommendation(
            disease_name=request.disease,
            confidence=request.confidence,
            gps_data=None,  # Would come from image EXIF if available
            user_location=user_location
        )
        
        # Extract prevention list
        prevention = recommendations.get('prevention', [])
        if isinstance(prevention, str):
            prevention = [prevention]
        
        # Format response for frontend
        response = {
            "disease": recommendations['disease'],
            "confidence_percent": recommendations['confidence'],
            "model_used": recommendations.get('model', 'Unknown'),
            "recommendations": {
                "fertilizer": recommendations['fertilizer'],
                "treatment": recommendations['treatment'],
                "prevention": prevention
            },
            "location": recommendations.get('location', {}),
            "note": recommendations['note']
        }
        response = apply_expert_recommendation_override(response, request.disease)
        return response
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
            gps_accuracy = exif_gps.accuracy
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
                source="upload"
            )
            detection_record.update(_save_upload_image_assets(record_id, image, result["image"]))
            
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
                                verification_status=PENDING_VERIFICATION_STATUS,
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
                                        verification_status=PENDING_VERIFICATION_STATUS,
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
        "total_detected": len(all_detections),
        "annotated_image_base64": encoded,
        "user_email": email,
        "message": f"{len(all_detections)} detections found ({len(high_confidence_detections)} saved - >= {UPLOAD_RECORD_CONFIDENCE_THRESHOLD:.0%} confidence)",
        "display_confidence_threshold": UPLOAD_DISPLAY_CONFIDENCE_THRESHOLD,
        "record_confidence_threshold": UPLOAD_RECORD_CONFIDENCE_THRESHOLD,
        # ✅ Return GPS data so frontend pins disease at correct location
        "gps_lat": lat,
        "gps_lng": lng,
        "gps_accuracy": gps_accuracy,
        "gps_source": gps_source
    }


# ─── CSV Export Endpoint ──────────────────────────────────────────────────────
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

    updates = {
        "verification_status": desired_status,
        "verified_by": decoded.get("email"),
        "verified_by_uid": decoded.get("uid"),
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    if desired_status == PENDING_VERIFICATION_STATUS:
        updates["verified_by"] = None
        updates["verified_by_uid"] = None
        updates["verified_at"] = None

    updated = _update_record_status_everywhere(user_id, record_id, updates)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")

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
    """Create or update an expert recommendation override."""
    if not is_expert_identity(decoded):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Expert access required")

    disease_key = normalize_disease_key(disease)
    overrides = load_expert_recommendations()
    overrides[disease_key] = {
        "disease": payload.disease or disease,
        "fertilizer": payload.fertilizer,
        "treatment": payload.treatment,
        "prevention": payload.prevention,
        "note": payload.note or "",
        "active": payload.active,
        "updated_by": decoded.get("email"),
        "updated_by_uid": decoded.get("uid"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_expert_recommendations(overrides)
    append_expert_audit_event(
        action="update_recommendation",
        actor=decoded,
        target={
            "disease": disease,
            "disease_key": disease_key,
        },
        details={
            "active": payload.active,
            "prevention_count": len(payload.prevention or []),
        },
    )
    return {
        "message": "Recommendation override saved",
        "disease": disease,
        "override": overrides[disease_key],
    }


@app.get("/expert/audit-log")
async def get_expert_audit_log(limit: int = 100, decoded: dict = Depends(verify_firebase_token)):
    """Get the latest expert audit events."""
    if not is_expert_identity(decoded):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Expert access required")

    return {
        "events": read_expert_audit_events(limit=limit),
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
    return {"status": "ok", "model": "YOLO11"}

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
    """Serve favicon endpoint"""
    # Return a 204 No Content response - browser will use the SVG data URI from HTML
    return Response(status_code=204)


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
