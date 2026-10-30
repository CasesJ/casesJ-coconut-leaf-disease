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
from io import BytesIO
from typing import Optional

import cv2
import numpy as np
from cv2 import IMREAD_COLOR, imdecode, imencode
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect, status
from PIL import Image, UnidentifiedImageError

from app.core.config import (
    IMAGE_INFERENCE_DAILY_LIMIT,
    IMAGE_INFERENCE_PER_MINUTE,
    MAX_IMAGE_UPLOAD_BYTES, MAX_IMAGE_UPLOAD_PIXELS,
    PENDING_VERIFICATION_STATUS,
    UPLOAD_DISPLAY_CONFIDENCE_THRESHOLD,
    UPLOAD_RECORD_CONFIDENCE_THRESHOLD,
    WS_CONNECTIONS_PER_USER,
    WS_FRAMES_PER_SECOND,
    WS_MAX_FRAME_BYTES,
)
from app.core.rate_limit import limiter
from app.core.security import verify_verified_firebase_token
from app.domain.detection import _extract_primary_disease, apply_record_defaults
from app.domain.recommendation import build_recommendation_response
from app.schemas.recommendation import FarmerRecommendationUpdate
from app.services.detection_service import (
    DetectionPersistenceError,
    find_record_for_user,
    save_detection_record,
    update_record_everywhere,
)
from app.services.farm_service import get_farm_center, set_initial_farm_center
from app.services.inference_service import InferenceUnavailable, run_image_inference
from app.services.media_service import present_detection_record

router = APIRouter(tags=["detections"])
logger = logging.getLogger(__name__)
_background_tasks: set[asyncio.Task] = set()


@router.post("/detect/image")
async def detect_image(
    request: Request,
    file: UploadFile = File(...),
    lat: Optional[float] = Form(None),
    lng: Optional[float] = Form(None),
    accuracy: Optional[float] = Form(None),
):
    """Detect disease in an uploaded image (POST-FLIGHT approach with EXIF GPS extraction)."""
    request_started = time.perf_counter()
    operation_id = uuid.uuid4().hex[:12]
    performance_ms: dict[str, float] = {}
    from model import detector  # noqa: PLC0415
    from drone_gps import get_drone_gps, init_drone_gps  # noqa: PLC0415
    from app.infrastructure.firebase.auth import verify_token  # noqa: PLC0415

    # --- Auth (requires a verified Firebase identity) ---
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication is required for image inference")
    try:
        decoded = await asyncio.to_thread(verify_token, auth_header.split(" ", 1)[1])
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid authentication token") from exc
    user_id = str(decoded.get("uid") or "").strip()
    if not user_id or decoded.get("token_verified_offline"):
        raise HTTPException(status_code=401, detail="Verified Firebase authentication is required")
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
    read_started = time.perf_counter()
    contents = await file.read(MAX_IMAGE_UPLOAD_BYTES + 1)
    performance_ms["upload_read_ms"] = (time.perf_counter() - read_started) * 1000
    if len(contents) > MAX_IMAGE_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image upload is too large")
    inference_contents = contents
    validation_started = time.perf_counter()
    try:
        with Image.open(BytesIO(contents)) as source_image:
            image_format = source_image.format
            if image_format not in {"JPEG", "MPO", "PNG", "WEBP", "BMP", "TIFF"}:
                raise ValueError(f"Unsupported image format: {source_image.format or 'unknown'}. Upload JPEG, MPO, PNG, WebP, BMP or TIFF")
            if image_format != "MPO" and getattr(source_image, "n_frames", 1) != 1:
                raise ValueError("Upload a single-frame photograph, not an animation or multi-page image")
            pixels = source_image.width * source_image.height
            if pixels > MAX_IMAGE_UPLOAD_PIXELS:
                raise ValueError(
                    f"Image resolution is {source_image.width} x {source_image.height} "
                    f"({pixels / 1_000_000:.2f} megapixels); "
                    f"the upload limit is {MAX_IMAGE_UPLOAD_PIXELS / 1_000_000:g} megapixels"
                )
            source_image.verify()
        if image_format == "MPO":
            # Read metadata from the untouched MPO first; convert only its primary
            # JPEG frame so inference and durable evidence use the same pixels.
            with Image.open(BytesIO(contents)) as mpo:
                mpo.seek(0)
                exif = mpo.info.get("exif")
                icc_profile = mpo.info.get("icc_profile")
                with mpo.convert("RGB") as primary:
                    output = BytesIO()
                    save_options = {"format": "JPEG", "quality": 100, "subsampling": 0}
                    if exif:
                        save_options["exif"] = exif
                    if icc_profile:
                        save_options["icc_profile"] = icc_profile
                    primary.save(output, **save_options)
                    inference_contents = output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=400, detail=str(exc) or "Upload must be a valid image") from exc
    performance_ms["validation_ms"] = (time.perf_counter() - validation_started) * 1000
    # The decoded signature identifies the format; camera/browser MIME labels
    # can be empty, generic or incorrect, especially for uppercase extensions.
    metadata_started = time.perf_counter()
    metadata_reader = get_drone_gps() or init_drone_gps()
    image_metadata = metadata_reader.extract_image_metadata(contents, file.filename)

    # --- GPS extraction ---
    drone_gps = get_drone_gps()
    # An upload is located only when its own embedded EXIF GPS is usable.
    # Do not use form/browser/map defaults for uploaded drone imagery.
    gps_source = "unknown"
    gps_accuracy = None
    if drone_gps:
        exif_gps = drone_gps.extract_gps_from_image(
            contents, file.filename, None, image_metadata,
        )
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
    performance_ms["metadata_gps_ms"] = (time.perf_counter() - metadata_started) * 1000

    decode_started = time.perf_counter()
    # --- Inference ---
    np_arr = np.frombuffer(inference_contents, np.uint8)
    image = imdecode(np_arr, IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="Upload must be a valid image")
    performance_ms["decode_ms"] = (time.perf_counter() - decode_started) * 1000
    # The first valid image with embedded coordinates establishes the farm,
    # even if it contains no disease detection.
    farm_center = None
    farm_center_task = None
    if lat is not None and lng is not None and gps_source in {"exif", "dji_exif"}:
        farm_lookup_started = time.perf_counter()

        def establish_farm_center():
            center = set_initial_farm_center(
                user_id, lat, lng, gps_source, operation_id=operation_id,
            )
            duration = (time.perf_counter() - farm_lookup_started) * 1000
            logger.info(
                "Farm centre operation operation_id=%s duration_ms=%.0f established=%s",
                operation_id, duration, bool(center),
            )
            return center, duration

        # This optional location initialization runs beside inference. It never
        # delays the required detection response if it is still in progress.
        farm_center_task = asyncio.create_task(asyncio.to_thread(establish_farm_center))
        _background_tasks.add(farm_center_task)
        farm_center_task.add_done_callback(_background_tasks.discard)
    await limiter.rolling(f"image-minute:{user_id}", IMAGE_INFERENCE_PER_MINUTE, 60)
    await limiter.image_quota_24h(f"image-quota:{user_id}", IMAGE_INFERENCE_DAILY_LIMIT)
    inference_started = time.perf_counter()
    try:
        result = await asyncio.to_thread(run_image_inference, detector, image, UPLOAD_DISPLAY_CONFIDENCE_THRESHOLD)
    except InferenceUnavailable as exc:
        logger.error("Image inference failed for upload by %s: %s", user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Disease analysis is temporarily unavailable. Your image was not saved.",
        ) from exc
    inference_ms = (time.perf_counter() - inference_started) * 1000
    performance_ms["inference_ms"] = inference_ms
    if farm_center_task is not None and farm_center_task.done():
        farm_center, farm_center_ms = farm_center_task.result()
        performance_ms["farm_center_lookup_ms"] = farm_center_ms
    else:
        performance_ms["farm_center_lookup_ms"] = 0.0
        if farm_center_task is not None:
            logger.info("Farm centre operation pending operation_id=%s", operation_id)
    all_detections = result["detections"]

    high_confidence_detections = [d for d in all_detections if d["confidence"] >= UPLOAD_RECORD_CONFIDENCE_THRESHOLD]

    # --- Encode a compact annotated preview ---
    # The full-resolution image is still analyzed and stored as evidence. Sending
    # that entire image back as base64 made the result wait on a large response.
    preview_started = time.perf_counter()
    preview_image = result["image"]
    preview_scale = min(1.0, 1280 / max(preview_image.shape[:2]))
    if preview_scale < 1.0:
        preview_image = cv2.resize(
            preview_image,
            (max(1, round(preview_image.shape[1] * preview_scale)),
             max(1, round(preview_image.shape[0] * preview_scale))),
            interpolation=cv2.INTER_AREA,
        )
    _, buffer = imencode(".jpg", preview_image, [cv2.IMWRITE_JPEG_QUALITY, 84])
    encoded = base64.b64encode(buffer).decode("utf-8")
    performance_ms["preview_encode_ms"] = (time.perf_counter() - preview_started) * 1000

    # --- Persist ---
    persistence_started = time.perf_counter()
    record_id = None
    if high_confidence_detections:
        gps_data = {
            "latitude": lat, "longitude": lng,
            "accuracy": gps_accuracy, "source": gps_source,
            "timestamp": datetime.now().isoformat(),
        }
        record_id = f"upload_{user_id}_{int(datetime.now(timezone.utc).timestamp() * 1000)}_{uuid.uuid4().hex[:8]}"
        persistence_timings: dict[str, float] = {}
        try:
            await asyncio.to_thread(
                save_detection_record,
                user_id=user_id, email=email, record_id=record_id,
                detections=high_confidence_detections, gps_data=gps_data,
                filename=file.filename, source="upload",
                image_metadata=image_metadata,
                original_bytes=inference_contents, annotated_image=result["image"],
                is_offline=is_offline,
                image_dimensions={"width": int(image.shape[1]), "height": int(image.shape[0])},
                timings=persistence_timings,
                operation_id=operation_id,
            )
        except DetectionPersistenceError as exc:
            logger.warning("New detection %s could not be durably saved: %s", record_id, exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"The detection was not saved: {exc}",
            ) from exc

        performance_ms.update(persistence_timings)
    performance_ms["persistence_total_ms"] = (time.perf_counter() - persistence_started) * 1000
    performance_ms["request_total_ms"] = (time.perf_counter() - request_started) * 1000
    logger.info("Upload stages operation_id=%s %s recorded=%s bytes=%d", operation_id, " ".join(
        f"{name}={duration:.0f}" for name, duration in performance_ms.items()),
        bool(record_id), len(contents))
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
        "image_dimensions": {"width": int(image.shape[1]), "height": int(image.shape[0])},
        "performance_ms": {name: round(duration, 1) for name, duration in performance_ms.items()},
        "operation_id": operation_id,
        "farm_center": farm_center,
    }


@router.get("/farm/location")
async def get_my_farm_location(decoded: dict = Depends(verify_verified_firebase_token)):
    """Return the authenticated user's established farm centre."""
    user_id = str(decoded.get("uid") or "").strip()
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Verified Firebase sign-in is required")
    return {"farm_center": get_farm_center(user_id)}


@router.put("/detections/my-records/{record_id}/low-confidence-recommendation")
async def save_farmer_low_confidence_recommendation(
    record_id: str,
    payload: FarmerRecommendationUpdate,
    decoded: dict = Depends(verify_verified_firebase_token),
):
    """Save a farmer review for a <50% confidence result."""
    user_id = str(decoded.get("uid") or "").strip()
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Verified Firebase sign-in is required")
    record = find_record_for_user(user_id, record_id)
    if not record or str(record.get("user_id") or "") != user_id:
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
    decoded: dict = Depends(verify_verified_firebase_token),
):
    """Get recent detection records for the logged-in user."""
    from app.services.detection_service import get_user_records  # noqa: PLC0415
    user_id = decoded.get("uid")
    email = decoded.get("email")
    safe_limit = max(1, min(limit, 200))
    records = get_user_records(user_id, limit=safe_limit)
    for record in records:
        apply_record_defaults(record)
    records = [present_detection_record(record) for record in records]
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
    from app.infrastructure.firebase.auth import verify_token  # noqa: PLC0415

    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=1008, reason="Authentication required")
        return
    try:
        decoded = verify_token(token)
        user_id = str(decoded.get("uid") or "").strip()
        if decoded.get("token_verified_offline") or not user_id:
            raise ValueError("A verified Firebase identity is required")
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
    persistence_queue = deque(maxlen=50)
    last_firebase_save = time.time()
    latency_times: deque = deque(maxlen=30)

    async def background_detection_saver():
        while True:
            try:
                if persistence_queue:
                    record = persistence_queue.popleft()
                    try:
                        await asyncio.to_thread(
                            save_detection_record,
                            user_id=user_id,
                            email=user_email,
                            record_id=record["record_id"],
                            detections=record["detections"],
                            gps_data=record["gps_data"],
                            filename=record["filename"],
                            source="drone",
                            image_metadata={},
                            original_bytes=record["original_bytes"],
                            annotated_image=record["annotated_image"],
                            is_offline=False,
                            image_dimensions=record["image_dimensions"],
                        )
                    except DetectionPersistenceError as error:
                        logger.warning("Streaming detection %s was not saved: %s",
                                       record["record_id"], error)
                    except Exception as error:
                        logger.warning("Streaming detection persistence failed (%s)",
                                       type(error).__name__)
                await asyncio.sleep(0.1)
            except Exception:
                await asyncio.sleep(0.5)

    saver_task = asyncio.create_task(background_detection_saver())

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

                try:
                    result = await asyncio.to_thread(run_image_inference, detector, image, 50)
                except InferenceUnavailable:
                    await websocket.send_json({
                        "error": "Disease analysis is temporarily unavailable",
                        "inference_status": "failed",
                        "recorded": False,
                        "frame": frame_count,
                    })
                    continue
                all_detections = result["detections"]
                high_confidence_detections = [d for d in all_detections if d["confidence"] >= 0.50]

                if high_confidence_detections and user_id and (time.time() - last_firebase_save) > 1.0:
                    try:
                        gps_pos = get_current_drone_position()
                        source_ok, source_buffer = imencode(
                            ".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92]
                        )
                        if source_ok:
                            record_id = f"drone_{int(time.time() * 1000)}_{uuid.uuid4().hex[:12]}"
                            if len(persistence_queue) < persistence_queue.maxlen:
                                persistence_queue.append({
                                    "record_id": record_id,
                                    "filename": f"stream-frame-{frame_count}.jpg",
                                    "detections": high_confidence_detections,
                                    "gps_data": {
                                        "latitude": (gps_pos or {}).get("latitude") if isinstance(gps_pos, dict) else None,
                                        "longitude": (gps_pos or {}).get("longitude") if isinstance(gps_pos, dict) else None,
                                        "source": "drone_stream",
                                        "timestamp": datetime.now(timezone.utc).isoformat(),
                                    },
                                    "original_bytes": source_buffer.tobytes(),
                                    "annotated_image": result["image"],
                                    "image_dimensions": {
                                        "width": int(image.shape[1]),
                                        "height": int(image.shape[0]),
                                    },
                                })
                                last_firebase_save = time.time()
                            else:
                                logger.warning("Streaming detection persistence queue is full")
                    except Exception:
                        logger.warning("Streaming detection could not be queued for persistence")

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
