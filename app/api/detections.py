"""
app/api/detections.py — Image upload detection and WebSocket stream endpoints.
"""
import asyncio
import base64
import logging
import math
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Optional

import cv2
import numpy as np
from cv2 import IMREAD_COLOR, imdecode, imencode
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect, status

from app.core.config import (
    IMAGE_INFERENCE_DAILY_LIMIT,
    IMAGE_INFERENCE_PER_MINUTE,
    MAX_IMAGE_UPLOAD_BYTES,
    PENDING_VERIFICATION_STATUS,
    UPLOAD_DISPLAY_CONFIDENCE_THRESHOLD,
    UPLOAD_RECORD_CONFIDENCE_THRESHOLD,
    WS_CONNECTIONS_PER_USER,
    WS_FRAMES_PER_SECOND,
    WS_MAX_FRAME_BYTES,
)
from app.core.rate_limit import limiter
from app.core.security import verify_firebase_token
from app.domain.detection import _extract_primary_disease, apply_record_defaults
from app.domain.recommendation import build_recommendation_response
from app.schemas.recommendation import FarmerRecommendationUpdate
from app.services.detection_service import (
    find_record_for_user,
    save_detection_record,
    update_record_everywhere,
)
from app.services.farm_service import get_farm_center, set_initial_farm_center

router = APIRouter(tags=["detections"])
logger = logging.getLogger(__name__)


@router.post("/detect/image")
async def detect_image(
    request: Request,
    file: UploadFile = File(...),
    lat: Optional[float] = Form(None),
    lng: Optional[float] = Form(None),
    accuracy: Optional[float] = Form(None),
):
    """Detect disease in an uploaded image (POST-FLIGHT approach with EXIF GPS extraction)."""
    from model import detector  # noqa: PLC0415
    from drone_gps import get_drone_gps, init_drone_gps  # noqa: PLC0415
    from app.infrastructure.firebase.auth import verify_token  # noqa: PLC0415

    # --- Auth (optional — supports offline mode) ---
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication is required for image inference")
    try:
        decoded = verify_token(auth_header.split(" ", 1)[1])
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid authentication token") from exc
    user_id = str(decoded.get("uid") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Token does not contain a user identity")
    email = decoded.get("email", "unknown")
    is_offline = False

    # --- Read upload ---
    declared_size = request.headers.get("content-length")
    # Content-Length includes multipart boundaries and form fields, hence the
    # small allowance; the actual file bytes are still capped exactly below.
    if declared_size:
        try:
            if int(declared_size) > MAX_IMAGE_UPLOAD_BYTES + 1_000_000:
                raise HTTPException(status_code=413, detail="Image upload is too large")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header")
    contents = await file.read(MAX_IMAGE_UPLOAD_BYTES + 1)
    if len(contents) > MAX_IMAGE_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image upload is too large")
    metadata_reader = get_drone_gps() or init_drone_gps()
    image_metadata = metadata_reader.extract_image_metadata(contents, file.filename)

    # --- GPS extraction ---
    drone_gps = get_drone_gps()
    # An upload is located only when its own embedded EXIF GPS is usable.
    # Do not use form/browser/map defaults for uploaded drone imagery.
    gps_source = "unknown"
    gps_accuracy = None
    if drone_gps:
        exif_gps = drone_gps.extract_gps_from_image(contents, file.filename)
        if (
            exif_gps
            and math.isfinite(exif_gps.latitude)
            and math.isfinite(exif_gps.longitude)
            and -90 <= exif_gps.latitude <= 90
            and -180 <= exif_gps.longitude <= 180
        ):
            lat, lng = exif_gps.latitude, exif_gps.longitude
            gps_source = exif_gps.source
            gps_accuracy = exif_gps.accuracy if exif_gps.accuracy is not None else 10.0
            # Keep the general image metadata and canonical GPS payload aligned
            # so every storage backend and the records UI see the same values.
            image_metadata["gps"] = {"latitude": lat, "longitude": lng}
            image_metadata["gps_source"] = gps_source
        else:
            lat, lng = None, None
            image_metadata["gps"] = None
            image_metadata["gps_source"] = "unknown"
    else:
        lat, lng = None, None

    # --- Inference ---
    np_arr = np.frombuffer(contents, np.uint8)
    image = imdecode(np_arr, IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="Upload must be a valid image")
    # The first valid image with embedded coordinates establishes the farm,
    # even if it contains no disease detection.
    farm_center = None
    if lat is not None and lng is not None and gps_source in {"exif", "dji_exif"}:
        farm_center = set_initial_farm_center(user_id, lat, lng, gps_source)
    await limiter.rolling(f"image-minute:{user_id}", IMAGE_INFERENCE_PER_MINUTE, 60)
    await limiter.image_quota_24h(f"image-quota:{user_id}", IMAGE_INFERENCE_DAILY_LIMIT)
    result = detector.predict(image, conf=UPLOAD_DISPLAY_CONFIDENCE_THRESHOLD)
    all_detections = result["detections"]

    high_confidence_detections = [d for d in all_detections if d["confidence"] >= UPLOAD_RECORD_CONFIDENCE_THRESHOLD]

    # --- Encode annotated image ---
    _, buffer = imencode(".jpg", result["image"], [cv2.IMWRITE_JPEG_QUALITY, 70])
    encoded = base64.b64encode(buffer).decode("utf-8")

    # --- Persist ---
    record_id = None
    if high_confidence_detections:
        gps_data = {
            "latitude": lat, "longitude": lng,
            "accuracy": gps_accuracy, "source": gps_source,
            "timestamp": datetime.now().isoformat(),
        }
        record_id = f"upload_{user_id}_{int(datetime.now(timezone.utc).timestamp() * 1000)}_{uuid.uuid4().hex[:8]}"
        save_detection_record(
            user_id=user_id, email=email, record_id=record_id,
            detections=high_confidence_detections, gps_data=gps_data,
            filename=file.filename, source="upload",
            image_metadata=image_metadata,
            original_bytes=contents, annotated_image=result["image"],
            is_offline=is_offline,
        )

    return {
        "detections": all_detections,
        "recorded_count": len(high_confidence_detections),
        "record_id": record_id,
        "total_detected": len(all_detections),
        "annotated_image_base64": encoded,
        "user_email": email,
        "message": f"{len(all_detections)} detections found ({len(high_confidence_detections)} saved)",
        "display_confidence_threshold": UPLOAD_DISPLAY_CONFIDENCE_THRESHOLD,
        "record_confidence_threshold": UPLOAD_RECORD_CONFIDENCE_THRESHOLD,
        "model": {
            "name": "YOLO26 v6",
            "backend": detector.backend,
            "weights": detector.active_model_path.name if detector.active_model_path else None,
            "classes": detector.class_names,
        },
        "gps_lat": lat, "gps_lng": lng, "gps_accuracy": gps_accuracy,
        "gps_source": gps_source, "gps_metadata_found": lat is not None and lng is not None,
        "image_metadata": image_metadata,
        "farm_center": farm_center,
    }


@router.get("/farm/location")
async def get_my_farm_location(decoded: dict = Depends(verify_firebase_token)):
    """Return the authenticated user's established farm centre."""
    return {"farm_center": get_farm_center(str(decoded.get("uid") or "").strip())}


@router.put("/detections/my-records/{record_id}/low-confidence-recommendation")
async def save_farmer_low_confidence_recommendation(
    record_id: str,
    payload: FarmerRecommendationUpdate,
    decoded: dict = Depends(verify_firebase_token),
):
    """Save a farmer review for a <50% confidence result."""
    user_id = str(decoded.get("uid") or "").strip()
    record = find_record_for_user(user_id, record_id)
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
    updated = update_record_everywhere(user_id, record_id, {
        "recommendation_snapshot": snapshot,
        "recommendation_source": "farmer_review",
        "farmer_recommendation_confirmed": True,
        "farmer_recommendation_confirmed_at": datetime.now(timezone.utc).isoformat(),
        "verification_status": PENDING_VERIFICATION_STATUS,
        "verified_by": None,
        "verified_by_uid": None,
        "verified_by_role": None,
        "verified_at": None,
    })
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")
    return {"message": "Farmer recommendation saved and sent for expert verification", "recommendation_snapshot": snapshot}


@router.get("/detections/my-records")
def get_user_detections_endpoint(
    limit: int = 100,
    decoded: dict = Depends(verify_firebase_token),
):
    """Get recent detection records for the logged-in user."""
    from app.services.detection_service import get_user_records  # noqa: PLC0415
    user_id = decoded.get("uid")
    email = decoded.get("email")
    safe_limit = max(1, min(limit, 200))
    records = get_user_records(user_id, limit=safe_limit)
    for record in records:
        apply_record_defaults(record)
    return {
        "email": email,
        "total_records": len(records),
        "limit": safe_limit,
        "records": records,
    }


@router.websocket("/detect/stream")
async def detect_stream(websocket: WebSocket):
    """Real-time webcam disease detection stream."""
    from model import detector  # noqa: PLC0415
    from drone_gps import get_current_drone_position  # noqa: PLC0415
    from firebase_admin import db  # noqa: PLC0415
    from app.infrastructure.firebase.auth import verify_token  # noqa: PLC0415

    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=1008, reason="Authentication required")
        return
    try:
        decoded = verify_token(token)
        user_id = str(decoded.get("uid") or "").strip()
        if not user_id:
            raise ValueError("Token does not contain a user identity")
        user_email = decoded.get("email", "unknown")
    except Exception:
        await websocket.close(code=1008, reason="Invalid authentication token")
        return

    connection_key = f"stream:{user_id}"
    try:
        await limiter.acquire_connection(connection_key, WS_CONNECTIONS_PER_USER)
    except HTTPException:
        await websocket.close(code=1013, reason="Too many streaming connections")
        return
    await websocket.accept()

    frame_buffer = deque(maxlen=2)
    frame_count = 0
    skip_count = 0
    firebase_queue = deque(maxlen=50)
    last_firebase_save = time.time()
    latency_times: deque = deque(maxlen=30)

    async def background_firebase_saver():
        while True:
            try:
                if firebase_queue:
                    record = firebase_queue.popleft()
                    if user_id:
                        db.reference(f"users/{user_id}/drone_log").push(record)
                await asyncio.sleep(0.1)
            except Exception:
                await asyncio.sleep(0.5)

    saver_task = asyncio.create_task(background_firebase_saver())

    try:
        while True:
            frame_start = time.time()
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=0.05)
            except asyncio.TimeoutError:
                skip_count += 1
                continue

            if len(data) > WS_MAX_FRAME_BYTES * 2:  # base64 expands image bytes.
                await websocket.send_json({"error": "Frame is too large"})
                await websocket.close(code=1009, reason="Frame too large")
                break
            try:
                await limiter.rolling(f"stream-frame:{user_id}", WS_FRAMES_PER_SECOND, 1)
            except HTTPException:
                await websocket.send_json({"error": "Frame rate limit exceeded"})
                continue

            frame_count += 1
            if frame_count % 2 != 0:
                skip_count += 1
                continue

            try:
                img_bytes = base64.b64decode(data.split(",")[-1], validate=True)
                if len(img_bytes) > WS_MAX_FRAME_BYTES:
                    await websocket.send_json({"error": "Frame is too large"})
                    continue
                np_arr = np.frombuffer(img_bytes, np.uint8)
                image = imdecode(np_arr, IMREAD_COLOR)
                if image is None:
                    continue

                result = detector.predict(image)
                all_detections = result["detections"]
                high_confidence_detections = [d for d in all_detections if d["confidence"] >= 0.50]

                if high_confidence_detections and user_id and (time.time() - last_firebase_save) > 1.0:
                    try:
                        gps_pos = get_current_drone_position()
                        firebase_queue.append({
                            "timestamp": datetime.now().isoformat(),
                            "frame": frame_count,
                            "detections": high_confidence_detections,
                            "count": len(high_confidence_detections),
                            "gps": gps_pos,
                        })
                        last_firebase_save = time.time()
                    except Exception:
                        pass

                _, buffer = imencode(".jpg", result["image"], [cv2.IMWRITE_JPEG_QUALITY, 70])
                encoded = base64.b64encode(buffer).decode("utf-8")

                try:
                    gps_position = get_current_drone_position()
                except Exception:
                    gps_position = None

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
                    "fps": round(1000 / avg_latency if avg_latency > 0 else 0, 1),
                })
            except Exception:
                continue
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Streaming detection failed")
    finally:
        saver_task.cancel()
        await limiter.release_connection(connection_key)
