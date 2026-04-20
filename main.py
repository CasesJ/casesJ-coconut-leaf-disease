import base64
import numpy as np
from typing import Any
from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect, Depends, HTTPException, status, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer
from contextlib import asynccontextmanager
from pydantic import BaseModel
from datetime import datetime
import asyncio
import threading
import time
from collections import deque

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

app = FastAPI(title="Coconut Leaf Disease Detector", version="1.0.0")
app.mount("/static", StaticFiles(directory="static"), name="static")

# ─── Startup/Shutdown Events ──────────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    """Initialize drone GPS connection on startup"""
    try:
        drone_gps = init_drone_gps(drone_ip="192.168.1.1", port=8889)
        connected = await drone_gps.connect()
        if connected:
            print("[OK] Drone GPS initialized")
        else:
            print("[WARN] Drone GPS connection failed (will use browser location)")
    except Exception as e:
        print(f"[WARN] Drone GPS init error: {str(e)}")

@app.on_event("shutdown")
async def shutdown_event():
    """Disconnect drone GPS on shutdown"""
    drone_gps = get_drone_gps()
    if drone_gps:
        await drone_gps.disconnect()

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

# ─── Authentication Endpoints ──────────────────────────────────────────────────
@app.post("/auth/verify-token", response_model=UserResponse)
async def verify_user_token(request: LoginRequest):
    """Verify Firebase ID token (called after Firebase login on client)"""
    try:
        decoded = verify_token(request.token)
        return {
            "uid": decoded.get('uid'),
            "email": decoded.get('email'),
            "message": "Token verified successfully"
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e)
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



# ─── Image Upload Endpoint ────────────────────────────────────────────────────
@app.post("/detect/image")
async def detect_image(file: UploadFile = File(...), lat: float = Form(None), lng: float = Form(None), decoded: dict = Depends(verify_firebase_token)):
    """Detect disease in uploaded image (shows all detections, saves if confidence >= 50%)"""
    user_id = decoded.get('uid')
    email = decoded.get('email')
    
    print(f"\n{'='*60}")
    print(f"[DETECT] DETECTION REQUEST STARTED")
    print(f"{'='*60}")
    print(f"[USER] Email: {email}")
    print(f"[USER] ID: {user_id}")
    print(f"[GPS] Location: lat={lat}, lng={lng}")
    
    contents = await file.read()
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
    
    # Save to Firebase only high-confidence detections
    if high_confidence_detections:
        try:
            print(f"\n[SAVE] SAVING DETECTION RECORD")
            print(f"   User: {user_id}")
            print(f"   Email: {email}")
            print(f"   Detections: {len(high_confidence_detections)}")
            
            detection_record = {
                'timestamp': datetime.now().isoformat(),
                'email': email,
                'detections': high_confidence_detections,
                'count': len(high_confidence_detections),
                'source': 'upload',
                'lat': lat,
                'lng': lng
            }
            
            saved = False
            
            # Try Realtime Database first
            try:
                print(f"   [RTDB] Attempting Realtime Database save...")
                ref = db.reference(f'users/{user_id}/uploads')
                push_result = ref.push(detection_record)
                print(f"[OK] SUCCESS: Saved to Realtime Database")
                print(f"   Path: users/{user_id}/uploads/{push_result.key}")
                saved = True
            except Exception as rtdb_error:
                print(f"[WARN] Realtime Database failed: {type(rtdb_error).__name__}")
                
                # Fallback to Firestore
                if FIRESTORE_AVAILABLE and not saved:
                    try:
                        print(f"   [FIRESTORE] Falling back to Firestore...")
                        doc_ref = fs.collection('users').document(user_id).collection('detections').add(detection_record)
                        print(f"[OK] SUCCESS: Saved to Firestore")
                        saved = True
                    except Exception as firestore_error:
                        print(f"[WARN] Firestore failed: {type(firestore_error).__name__}")
                
                # Final fallback: Local JSON storage
                if not saved:
                    print(f"   [LOCAL] Using local storage (records persist locally)...")
                    if save_detection(user_id, email, high_confidence_detections):
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
        "message": f"{len(all_detections)} detections found ({len(high_confidence_detections)} saved - >= 50% confidence)"
    }


# ─── Video Detection Endpoint (Post-Processing) ────────────────────────────────
# ─── Fertilizer Recommendation Endpoint ────────────────────────────────────────
class RecommendationRequest(BaseModel):
    disease: str
    confidence: float

@app.post("/recommendations/fertilizer")
async def get_fertilizer_recommendation(request: RecommendationRequest, decoded: dict = Depends(verify_firebase_token)):
    """
    Get farmer-friendly fertilizer recommendations based on detected disease.
    
    Args:
        disease: Disease name from detection
        confidence: Confidence level (0-1 or 0-100)
    
    Returns:
        Formatted recommendations for Fertilizer, Treatment, and Prevention
    """
    recommendation = detector.get_fertilizer_recommendation(request.disease, request.confidence)
    
    return {
        "disease": recommendation['disease'],
        "confidence_percent": recommendation['confidence'],
        "recommendations": {
            "fertilizer": recommendation['fertilizer'],
            "treatment": recommendation['treatment'],
            "prevention": recommendation['prevention']
        },
        "note": recommendation['note'],
        "location": "Davao, Philippines"
    }


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
    
    print(f"\n{'='*60}")
    print(f"[OK] TOTAL RECORDS RETRIEVED: {len(all_records)}")
    print(f"{'='*60}\n")
    
    return {
        'email': email,
        'total_records': len(all_records),
        'records': all_records
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
    return {"status": "ok"}


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