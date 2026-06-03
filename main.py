import base64
import numpy as np
import logging
from typing import Any, Optional
from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect, Depends, HTTPException, status, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pydantic import BaseModel
from datetime import datetime
import asyncio
import threading
import time
from collections import deque
import json
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
from firebase_config import verify_token
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

# ── Local Storage Compatibility Wrappers ──
def save_detection(user_id: str, email: str, inference_results: dict, gps_data: dict = None) -> bool:
    """Save detection to local storage - compatibility wrapper"""
    try:
        storage = get_local_storage()
        record = DetectionRecord(
            user_id=user_id,
            email=email,
            inference_results=inference_results,
            gps_data=gps_data,
            timestamp=datetime.utcnow().isoformat()
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
    message: str


class RecommendationRequest(BaseModel):
    disease: str
    confidence: float
    
    class Config:
        # Provide better error messages
        json_schema_extra = {
            "example": {
                "disease": "Cercospora",
                "confidence": 0.95
            }
        }


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
        print(f"{'='*60}\n")
        
        return {
            "uid": decoded.get('uid'),
            "email": decoded.get('email'),
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
    return {
        "uid": decoded.get('uid'),
        "email": decoded.get('email'),
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
        return {
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
    Detect disease in uploaded image (shows all detections, saves if confidence >= 50%)
    
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

    result = detector.predict(image)
    all_detections = result["detections"]
    print(f"[DETECT] Total Detections: {len(all_detections)}")
    
    # Filter: Only SAVE detections with >= 50% confidence to Firebase
    high_confidence_detections = [d for d in all_detections if d["confidence"] >= 0.50]
    print(f"[FILTER] High Confidence (>=50%): {len(high_confidence_detections)}")

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
            detection_record = {
                'timestamp': datetime.now().isoformat(),
                'email': email,
                'user_id': user_id,
                'detections': high_confidence_detections,
                'count': len(high_confidence_detections),
                'source': 'upload',
                'gps_data': gps_data,
                'filename': file.filename
            }
            
            saved = False
            
            # OFFLINE MODE: Skip Firebase, save to local storage only
            # Also save to local storage in online mode (hybrid approach)
            if is_offline:
                print(f"   [OFFLINE] Saving to local storage only...")
                if save_detection(user_id, email, high_confidence_detections, gps_data):
                    print(f"[OK] SUCCESS: Saved to local storage (offline mode)")
                    saved = True
            else:
                # Try Realtime Database first
                try:
                    print(f"   [RTDB] Attempting Realtime Database save...")
                    ref = db.reference(f'users/{user_id}/uploads')
                    push_result = ref.push(detection_record)
                    print(f"[OK] SUCCESS: Saved to Realtime Database")
                    print(f"   Path: users/{user_id}/uploads/{push_result.key}")
                    
                    # Also save to local storage (hybrid)
                    try:
                        save_detection(user_id, email, high_confidence_detections, gps_data)
                    except:
                        pass
                    
                    saved = True
                except Exception as rtdb_error:
                    print(f"[WARN] Realtime Database failed: {type(rtdb_error).__name__}")
                    
                    # Fallback to Firestore
                    if FIRESTORE_AVAILABLE and not saved:
                        try:
                            print(f"   [FIRESTORE] Falling back to Firestore...")
                            doc_ref = fs.collection('users').document(user_id).collection('detections').add(detection_record)
                            print(f"[OK] SUCCESS: Saved to Firestore")
                            
                            # Also save to local storage (hybrid)
                            try:
                                save_detection(user_id, email, high_confidence_detections, gps_data)
                            except:
                                pass
                            
                            saved = True
                        except Exception as firestore_error:
                            print(f"[WARN] Firestore failed: {type(firestore_error).__name__}")
                    
                    # Final fallback: Local JSON storage
                    if not saved:
                        print(f"   [LOCAL] Using local storage (records persist locally)...")
                        if save_detection(user_id, email, high_confidence_detections, gps_data):
                            print(f"[OK] SUCCESS: Saved to local storage")
                            saved = True
                    
        except Exception as firebase_error:
            print(f"[ERROR] ERROR: {type(firebase_error).__name__}: {firebase_error}")
    else:
        print(f"[WARN] No high-confidence detections to save (all < 50%)")
    
    print(f"{'='*60}\n")

    return {
        "detections": all_detections,
        "recorded_count": len(high_confidence_detections),
        "total_detected": len(all_detections),
        "annotated_image_base64": encoded,
        "user_email": email,
        "message": f"{len(all_detections)} detections found ({len(high_confidence_detections)} saved - >= 50% confidence)",
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
        all_records.extend(local_records)
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
                record['source'] = 'rtdb'
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
                record['source'] = 'rtdb_drone'
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
                record['source'] = 'firestore'
                all_records.append(record)
            
            if all_records:
                print(f"   [OK] Found {len(all_records)} records in Firestore")
        except Exception as firestore_error:
            print(f"   [WARN] Firestore Error: {type(firestore_error).__name__}")
    
    # Sort by timestamp (newest first)
    all_records.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    
    # ✅ Normalize records for frontend: extract lat/lng from gps_data
    for record in all_records:
        # Try to extract lat/lng from gps_data (handle both dict and JSON string)
        if isinstance(record.get('gps_data'), dict):
            # Extract from nested structure
            record['lat'] = record['gps_data'].get('latitude')
            record['lng'] = record['gps_data'].get('longitude')
            record['gps_source'] = record['gps_data'].get('source', 'unknown')
        elif isinstance(record.get('gps_data'), str):
            # Parse JSON string if needed
            try:
                gps = json.loads(record['gps_data'])
                record['lat'] = gps.get('latitude')
                record['lng'] = gps.get('longitude')
                record['gps_source'] = gps.get('source', 'unknown')
            except:
                pass
        
        # Also check for direct lat/lng fields (from some sources)
        if 'gps_lat' in record:
            record['lat'] = record.get('lat') or record['gps_lat']
        if 'gps_lng' in record:
            record['lng'] = record.get('lng') or record['gps_lng']
        if 'gps_source' in record and not record.get('gps_source'):
            record['gps_source'] = record['gps_source']
        
        # Ensure we have detections array in correct format
        if not record.get('detections') and record.get('inference_results'):
            if isinstance(record['inference_results'], str):
                try:
                    record['detections'] = json.loads(record['inference_results'])
                except:
                    record['detections'] = []
            else:
                record['detections'] = record['inference_results']
        
        # Ensure detections is a list
        if not isinstance(record.get('detections'), list):
            record['detections'] = []
    
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
            all_records.extend(local_records)
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
                    record['type'] = 'upload'
                    record['source'] = 'rtdb'
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
                    record['type'] = 'drone'
                    record['source'] = 'rtdb_drone'
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
                    record['type'] = 'upload'
                    record['source'] = 'firestore'
                    all_records.append(record)
                
                if all_records:
                    print(f"   [OK] Found {len(all_records)} records in Firestore")
            except Exception as firestore_error:
                print(f"   [WARN] Firestore Error: {type(firestore_error).__name__}")
        
        # Sort by timestamp (newest first)
        all_records.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        
        # ✅ Normalize records for frontend: extract lat/lng from gps_data
        for record in all_records:
            if isinstance(record.get('gps_data'), dict):
                record['lat'] = record['gps_data'].get('latitude')
                record['lng'] = record['gps_data'].get('longitude')
                record['gps_source'] = record['gps_data'].get('source', 'unknown')
            elif isinstance(record.get('gps_data'), str):
                try:
                    gps = json.loads(record['gps_data'])
                    record['lat'] = gps.get('latitude')
                    record['lng'] = gps.get('longitude')
                    record['gps_source'] = gps.get('source', 'unknown')
                except:
                    pass
            
            if 'gps_lat' in record:
                record['lat'] = record.get('lat') or record['gps_lat']
            if 'gps_lng' in record:
                record['lng'] = record.get('lng') or record['gps_lng']
            
            # Ensure we have detections array in correct format
            if not record.get('detections') and record.get('inference_results'):
                if isinstance(record['inference_results'], str):
                    try:
                        record['detections'] = json.loads(record['inference_results'])
                    except:
                        record['detections'] = []
                else:
                    record['detections'] = record['inference_results']
            
            if not isinstance(record.get('detections'), list):
                record['detections'] = []
        
        print(f"\n   [OK] Total records for user {user_id}: {len(all_records)}")
        
        return all_records

    except Exception as e:
        print(f"   [ERROR] Error fetching records: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching records: {str(e)}"
        )


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


if __name__ == "__main__":
    import uvicorn
    print("\n" + "="*70)
    print("  [START] Starting Coconut Disease Detector API")
    print("="*70)
    print("  [WEB] Access the web interface at: http://localhost:8000")
    print("  [DOCS] API documentation at: http://localhost:8000/docs")
    print("  [EXIT] Press Ctrl+C to stop the server")
    print("="*70 + "\n")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )