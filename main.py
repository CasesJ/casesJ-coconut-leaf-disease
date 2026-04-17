import cv2
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
from local_storage import save_detection, get_user_detections

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
            print("✅ Drone GPS initialized")
        else:
            print("⚠️  Drone GPS connection failed (will use browser location)")
    except Exception as e:
        print(f"⚠️  Drone GPS init error: {str(e)}")

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
    print(f"🔍 DETECTION REQUEST STARTED")
    print(f"{'='*60}")
    print(f"📧 User Email: {email}")
    print(f"👤 User ID: {user_id}")
    print(f"📍 GPS Location: lat={lat}, lng={lng}")
    
    contents = await file.read()
    np_arr = np.frombuffer(contents, np.uint8)
    image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    result = detector.predict(image)
    all_detections = result["detections"]
    print(f"🎯 Total Detections: {len(all_detections)}")
    
    # ✅ FILTER: Only SAVE detections with >= 50% confidence to Firebase
    high_confidence_detections = [d for d in all_detections if d["confidence"] >= 0.50]
    print(f"💾 High Confidence (>=50%): {len(high_confidence_detections)}")

    _, buffer = cv2.imencode(".jpg", result["image"])
    encoded = base64.b64encode(buffer).decode("utf-8")
    
    # Save to Firebase only high-confidence detections
    if high_confidence_detections:
        try:
            print(f"\n💾 SAVING DETECTION RECORD")
            print(f"   User: {user_id}")
            print(f"   Email: {email}")
            print(f"   Detections: {len(high_confidence_detections)}")
            
            detection_record = {
                'timestamp': datetime.now().isoformat(),
                'email': email,
                'detections': high_confidence_detections,
                'count': len(high_confidence_detections),
                'source': 'upload',
                'lat': lat,  # ✅ Include coordinates
                'lng': lng
            }
            
            saved = False
            
            # Try Realtime Database first
            try:
                print(f"   📤 Attempting Realtime Database save...")
                ref = db.reference(f'users/{user_id}/uploads')
                push_result = ref.push(detection_record)
                print(f"✅ SUCCESS: Saved to Realtime Database")
                print(f"   Path: users/{user_id}/uploads/{push_result.key}")
                saved = True
            except Exception as rtdb_error:
                print(f"⚠️  Realtime Database failed: {type(rtdb_error).__name__}")
                
                # Fallback to Firestore
                if FIRESTORE_AVAILABLE and not saved:
                    try:
                        print(f"   📤 Falling back to Firestore...")
                        doc_ref = fs.collection('users').document(user_id).collection('detections').add(detection_record)
                        print(f"✅ SUCCESS: Saved to Firestore")
                        saved = True
                    except Exception as firestore_error:
                        print(f"⚠️  Firestore failed: {type(firestore_error).__name__}")
                
                # Final fallback: Local JSON storage
                if not saved:
                    print(f"   💾 Using local storage (records persist locally)...")
                    if save_detection(user_id, email, high_confidence_detections):
                        print(f"✅ SUCCESS: Saved to local storage")
                        saved = True
                    
        except Exception as firebase_error:
            print(f"❌ ERROR: {type(firebase_error).__name__}: {firebase_error}")
    else:
        print(f"⚠️  No high-confidence detections to save (all < 50%)")
    
    print(f"{'='*60}\n")

    return {
        "detections": all_detections,
        "recorded_count": len(high_confidence_detections),
        "total_detected": len(all_detections),
        "annotated_image_base64": encoded,
        "user_email": email,
        "message": f"{len(all_detections)} detections found ({len(high_confidence_detections)} saved - >= 50% confidence)"
    }



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
    print(f"\n   💾 Checking local storage...")
    local_records = get_user_detections(user_id)
    if local_records:
        all_records.extend(local_records)
        print(f"   ✅ Found {len(local_records)} records in local storage")
    
    # Try Realtime Database - get both uploads and drone_log
    try:
        print(f"\n   📤 Checking Realtime Database (uploads)...")
        uploads_ref = db.reference(f'users/{user_id}/uploads')
        uploads_data = uploads_ref.get()
        
        if uploads_data:
            print(f"   ✅ Found {len(uploads_data)} upload records in RTDB")
            for key, record in uploads_data.items():
                record['id'] = key
                record['type'] = 'upload'
                record['source'] = 'rtdb'
                all_records.append(record)
        else:
            print(f"   ℹ️  No upload records in RTDB")
    except Exception as rtdb_error:
        print(f"   ⚠️  RTDB Upload Error: {type(rtdb_error).__name__}")
    
    # ✅ Also check drone_log records
    try:
        print(f"\n   📤 Checking Realtime Database (drone_log)...")
        drone_ref = db.reference(f'users/{user_id}/drone_log')
        drone_data = drone_ref.get()
        
        if drone_data:
            print(f"   ✅ Found {len(drone_data)} drone records in RTDB")
            for key, record in drone_data.items():
                record['id'] = key
                record['type'] = 'drone'
                record['source'] = 'rtdb_drone'
                all_records.append(record)
        else:
            print(f"   ℹ️  No drone records in RTDB")
    except Exception as drone_error:
        print(f"   ⚠️  RTDB Drone Error: {type(drone_error).__name__}")
    
    # Try Firestore as last resort
    if FIRESTORE_AVAILABLE and len(all_records) < 5:  # Only check if we have few records
        try:
            print(f"\n   📤 Checking Firestore...")
            docs = fs.collection('users').document(user_id).collection('detections').stream()
            for doc in docs:
                record = doc.to_dict()
                record['id'] = doc.id
                record['type'] = 'upload'
                record['source'] = 'firestore'
                all_records.append(record)
            
            if all_records:
                print(f"   ✅ Found {len(all_records)} records in Firestore")
        except Exception as firestore_error:
            print(f"   ⚠️  Firestore Error: {type(firestore_error).__name__}")
    
    # Sort by timestamp (newest first)
    all_records.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    
    print(f"\n{'='*60}")
    print(f"✅ TOTAL RECORDS RETRIEVED: {len(all_records)}")
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
    
    try:
        frame_count = 0
        while True:
            # Receive base64 frame from browser
            data = await websocket.receive_text()
            img_bytes = base64.b64decode(data.split(",")[-1])
            np_arr = np.frombuffer(img_bytes, np.uint8)
            image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

            result = detector.predict(image)
            all_detections = result["detections"]
            
            # ✅ FILTER: Only SAVE detections with >= 50% confidence to Firebase
            high_confidence_detections = [d for d in all_detections if d["confidence"] >= 0.50]
            
            # Save to Firebase only if meets threshold and user is authenticated
            if high_confidence_detections and user_id:
                frame_count += 1
                # Only save every 10 frames to avoid too many database writes
                if frame_count % 10 == 0:
                    try:
                        # Get drone GPS position
                        gps_pos = get_current_drone_position()
                        drone_record = {
                            'timestamp': datetime.now().isoformat(),
                            'frame': frame_count,
                            'detections': high_confidence_detections,
                            'count': len(high_confidence_detections),
                            'gps': gps_pos  # ✅ Include drone GPS coordinates
                        }
                        db.reference(f'users/{user_id}/drone_log').push(drone_record)
                    except Exception as firebase_error:
                        print(f"⚠️  Firebase save failed: {firebase_error}")
                        # Don't crash - stream still works!

            _, buffer = cv2.imencode(".jpg", result["image"])
            encoded = base64.b64encode(buffer).decode("utf-8")
            
            # ✅ Get drone GPS for WebSocket response
            gps_position = get_current_drone_position()

            await websocket.send_json({
                "detections": all_detections,
                "total_in_frame": len(all_detections),
                "annotated_image": f"data:image/jpeg;base64,{encoded}",
                "user_email": user_email,
                "gps": gps_position,  # ✅ Include GPS in real-time stream
                "recorded": len(high_confidence_detections) > 0
            })
    except WebSocketDisconnect:
        print(f"Client {user_email} disconnected")


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