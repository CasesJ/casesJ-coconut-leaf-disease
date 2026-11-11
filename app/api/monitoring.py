"""Authenticated treatment monitoring endpoints."""

import logging
from starlette.concurrency import run_in_threadpool
import re
from datetime import date, datetime, timedelta
from io import BytesIO
from uuid import uuid4
from zoneinfo import ZoneInfo

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from app.core.config import (IMAGE_INFERENCE_DAILY_LIMIT, IMAGE_INFERENCE_PER_MINUTE,
                             INFERENCE_DECODE_MAX_PIXELS, MAX_IMAGE_UPLOAD_BYTES,
                             MAX_IMAGE_UPLOAD_PIXELS, UPLOAD_DISPLAY_CONFIDENCE_THRESHOLD)
from app.core.rate_limit import limiter
from app.core.security import verify_verified_firebase_token as verify_firebase_token
from app.domain.expert import append_expert_audit_event, is_authorized_expert_identity
from app.api.detections import _mpo_primary_jpeg_bytes
from app.infrastructure.cloudinary_storage import delete_asset, upload_asset
from app.infrastructure.imaging.tree_reference import read_reference_image, reference_metadata
from app.services.detection_service import _annotated_detections
from app.services import media_service
from app.schemas.monitoring import (FollowUpRecord, FollowUpReview,
                                    FollowUpScheduleUpdate, MonitoringCaseCreate,
                                    PlannedTreatmentCreate, PlannedTreatmentRecord,
                                    TreatmentApplicationCreate,
                                    TreatmentApplicationRecord)
from app.services import monitoring_service as store
from app.services.inference_service import InferenceUnavailable, run_image_inference

router = APIRouter(tags=["monitoring"])
logger = logging.getLogger(__name__)
_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _uid(decoded: dict) -> str:
    uid = str(decoded.get("uid") or "").strip()
    if not uid or not _ID.fullmatch(uid) or decoded.get("token_verified_offline"):
        raise HTTPException(401, "Token does not contain a user identity")
    return uid


def _case_id(case_id: str) -> None:
    if not _ID.fullmatch(case_id):
        raise HTTPException(404, "Monitoring case not found")


def _require_case(farmer_id: str, case_id: str) -> dict:
    """Read a case in the already-authorized farmer scope or preserve the 404."""
    case = store.get_case(farmer_id, case_id)
    if not case:
        raise HTTPException(404, "Monitoring case not found")
    return case


def _farmer(decoded: dict) -> str:
    uid = _uid(decoded)
    if _trusted_expert(decoded):
        raise HTTPException(403, "Farmer access required")
    return uid


def _expert(decoded: dict) -> str:
    uid = _uid(decoded)
    if not _trusted_expert(decoded):
        raise HTTPException(403, "Expert access required")
    return uid


def _trusted_expert(decoded: dict) -> bool:
    # Preserve the wrapper's historical failure for a missing identity.
    if decoded is None:
        raise AttributeError("'NoneType' object has no attribute 'get'")
    return is_authorized_expert_identity(decoded)


_PHILIPPINE_TZ = ZoneInfo("Asia/Manila")


def _today_philippine() -> date:
    return datetime.now(_PHILIPPINE_TZ).date()


def _audit_timestamp() -> str:
    return datetime.now(_PHILIPPINE_TZ).isoformat()


@router.post("/expert/monitoring/cases", status_code=201)
def create_monitoring_case(payload: MonitoringCaseCreate,
                           decoded: dict = Depends(verify_firebase_token)):
    expert_id = _expert(decoded)
    due = payload.next_follow_up_date
    if due and due <= _today_philippine():
        raise HTTPException(422, "Next follow-up date must be in the future")
    try:
        case = store.create_case(payload.farmer_id, payload.tree_id,
                                 payload.baseline_detection_id,
                                 due.isoformat() if due else None,
                                 expert_id, payload.follow_up_interval_days)
        # The audit log is a detection-oriented view.  Record the successful
        # monitoring enrolment against its baseline upload so its displayed
        # timestamp is the time the expert added the disease to monitoring.
        append_expert_audit_event(
            action="create_monitoring_case",
            actor=decoded,
            target={
                "user_id": payload.farmer_id,
                "record_id": payload.baseline_detection_id,
                "tree_id": case.get("tree_id"),
            },
            details={
                "case_id": case.get("id"),
                "disease": case.get("disease"),
                "created_at": case.get("created_at"),
            },
        )
        return media_service.present_monitoring_case(case)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logger.exception("Could not create monitoring case")
        raise HTTPException(503, "Monitoring storage unavailable") from exc


@router.get("/monitoring/cases")
def my_monitoring_cases(decoded: dict = Depends(verify_firebase_token)):
    return {"cases": [media_service.present_monitoring_case(case)
                       for case in store.list_cases(_farmer(decoded))]}


@router.get("/expert/monitoring/cases")
def all_monitoring_cases(decoded: dict = Depends(verify_firebase_token)):
    _expert(decoded)
    return {"cases": [media_service.present_monitoring_case(case)
                       for case in store.list_expert_cases()]}


@router.get("/monitoring/cases/{case_id}")
def my_monitoring_case(case_id: str, decoded: dict = Depends(verify_firebase_token)):
    _case_id(case_id)
    case = _require_case(_farmer(decoded), case_id)
    return media_service.present_monitoring_case(case)


@router.get("/monitoring/cases/{case_id}/overview")
def my_monitoring_overview(case_id: str, decoded: dict = Depends(verify_firebase_token)):
    _case_id(case_id)
    overview = store.get_overview(_farmer(decoded), case_id)
    if not overview:
        raise HTTPException(404, "Monitoring case not found")
    overview["follow_up_upload_allowance"] = store.follow_up_upload_allowance(
        overview["case"]["farmer_id"], overview["case"]["tree_id"])
    return media_service.present_monitoring_overview(overview)


@router.get("/monitoring/cases/{case_id}/planned-treatments")
def my_planned_treatments(case_id: str, decoded: dict = Depends(verify_firebase_token)):
    _case_id(case_id)
    farmer_id = _farmer(decoded)
    _require_case(farmer_id, case_id)
    return {"planned_treatments": store.list_planned_treatments(farmer_id, case_id)}


@router.post("/monitoring/cases/{case_id}/planned-treatments", status_code=201)
def create_planned_treatment(case_id: str, payload: PlannedTreatmentCreate,
                             decoded: dict = Depends(verify_firebase_token)):
    _case_id(case_id)
    farmer_id = _farmer(decoded)
    _require_case(farmer_id, case_id)
    if payload.planned_date <= _today_philippine():
        raise HTTPException(422, "Planned treatment date must be in the future")
    planned = PlannedTreatmentRecord(
        id=uuid4().hex, case_id=case_id, farmer_id=farmer_id,
        planned_date=payload.planned_date,
        notes=payload.notes.strip() if payload.notes else None,
        status="scheduled", created_at=_audit_timestamp(),
        created_by_farmer_uid=farmer_id,
    ).model_dump(mode="json")
    try:
        store.save_planned_treatment(farmer_id, case_id, planned)
    except Exception as exc:
        logger.exception("Could not persist planned treatment for %s/%s", farmer_id, case_id)
        raise HTTPException(503, "Planned treatment storage unavailable") from exc
    return planned


@router.get("/monitoring/cases/{case_id}/treatment-applications")
def my_treatment_applications(case_id: str, decoded: dict = Depends(verify_firebase_token)):
    _case_id(case_id)
    farmer_id = _farmer(decoded)
    _require_case(farmer_id, case_id)
    return {"treatment_applications": store.list_treatment_applications(farmer_id, case_id)}


@router.post("/monitoring/cases/{case_id}/treatment-applications", status_code=201)
def create_treatment_application(case_id: str, payload: TreatmentApplicationCreate,
                                 decoded: dict = Depends(verify_firebase_token)):
    """Append what the farmer actually applied, separate from the care plan."""
    _case_id(case_id)
    farmer_id = _farmer(decoded)
    case = _require_case(farmer_id, case_id)
    if payload.application_date > _today_philippine():
        raise HTTPException(422, "Application date cannot be in the future")
    treatment_performed = payload.treatment_performed.strip()
    if not treatment_performed:
        raise HTTPException(422, "Treatment performed is required")
    notes = payload.notes.strip() if payload.notes else None
    planned_id = payload.planned_treatment_id
    if planned_id:
        planned = store.get_planned_treatment(farmer_id, case_id, planned_id)
        if not planned:
            raise HTTPException(404, "Planned treatment not found")
        if planned.get("status") != "scheduled":
            raise HTTPException(409, "Planned treatment has already been recorded as applied")
    interval_days = int(case.get("follow_up_interval_days") or 6)
    if interval_days < 1 or interval_days > 90:
        interval_days = 6
    due_date = payload.application_date + timedelta(days=interval_days)
    now = _audit_timestamp()
    application_id = uuid4().hex
    application = TreatmentApplicationRecord(
        id=application_id,
        case_id=case_id,
        farmer_id=farmer_id,
        treatment_performed=treatment_performed,
        application_date=payload.application_date,
        notes=notes or None,
        planned_treatment_id=planned_id,
        follow_up_interval_days=interval_days,
        first_follow_up_due_date=due_date,
        created_at=now,
        recorded_by_farmer_uid=farmer_id,
    ).model_dump(mode="json")
    follow_up_schedule = {
        "id": uuid4().hex, "case_id": case_id, "farmer_id": farmer_id,
        "schedule_type": "post_treatment", "due_date": due_date.isoformat(),
        "follow_up_interval_days": interval_days,
        "treatment_application_id": application_id,
        "created_at": now, "created_by_uid": farmer_id,
        "created_by_role": "farmer", "status": "scheduled",
    }
    try:
        store.save_treatment_application(farmer_id, case_id, application, follow_up_schedule)
    except Exception as exc:
        logger.exception("Could not persist treatment application for %s/%s", farmer_id, case_id)
        raise HTTPException(503, "Treatment application storage unavailable") from exc
    return application


@router.get("/expert/monitoring/cases/{farmer_id}/{case_id}")
def expert_monitoring_case(farmer_id: str, case_id: str,
                           decoded: dict = Depends(verify_firebase_token)):
    _expert(decoded)
    _case_id(case_id)
    if not _ID.fullmatch(farmer_id):
        raise HTTPException(404, "Monitoring case not found")
    case = _require_case(farmer_id, case_id)
    return media_service.present_monitoring_case(case)


@router.get("/expert/monitoring/cases/{farmer_id}/{case_id}/overview")
def expert_monitoring_overview(farmer_id: str, case_id: str,
                               decoded: dict = Depends(verify_firebase_token)):
    _expert(decoded)
    _case_id(case_id)
    if not _ID.fullmatch(farmer_id):
        raise HTTPException(404, "Monitoring case not found")
    overview = store.get_overview(farmer_id, case_id)
    if not overview:
        raise HTTPException(404, "Monitoring case not found")
    return media_service.present_monitoring_overview(overview, expert=True)


@router.get("/expert/monitoring/cases/{farmer_id}/{case_id}/treatment-applications")
def expert_treatment_applications(farmer_id: str, case_id: str,
                                  decoded: dict = Depends(verify_firebase_token)):
    _expert(decoded)
    _case_id(case_id)
    if not _ID.fullmatch(farmer_id) or not store.get_case(farmer_id, case_id):
        raise HTTPException(404, "Monitoring case not found")
    return {"treatment_applications": store.list_treatment_applications(farmer_id, case_id)}


@router.get("/expert/monitoring/cases/{farmer_id}/{case_id}/follow-ups")
def expert_follow_ups(farmer_id: str, case_id: str,
                      decoded: dict = Depends(verify_firebase_token)):
    _expert(decoded)
    _case_id(case_id)
    if not _ID.fullmatch(farmer_id) or not store.get_case(farmer_id, case_id):
        raise HTTPException(404, "Monitoring case not found")
    return {"follow_ups": [media_service.present_monitoring_follow_up(item, expert=True)
                           for item in store.list_follow_ups(farmer_id, case_id)]}


@router.put("/expert/monitoring/cases/{farmer_id}/{case_id}/schedule")
def reschedule_case(farmer_id: str, case_id: str, payload: FollowUpScheduleUpdate,
                    decoded: dict = Depends(verify_firebase_token)):
    expert_id = _expert(decoded)
    _case_id(case_id)
    if not _ID.fullmatch(farmer_id):
        raise HTTPException(404, "Monitoring case not found")
    if payload.next_follow_up_date <= _today_philippine():
        raise HTTPException(422, "Next follow-up date must be in the future")
    updated = store.update_next_follow_up(farmer_id, case_id,
                                          payload.next_follow_up_date.isoformat(), expert_id,
                                          payload.follow_up_interval_days)
    if not updated:
        raise HTTPException(404, "Monitoring case not found")
    return media_service.present_monitoring_case(updated)


@router.put("/expert/monitoring/cases/{farmer_id}/{case_id}/follow-ups/{follow_up_id}/review")
def confirm_follow_up(farmer_id: str, case_id: str, follow_up_id: str,
                      payload: FollowUpReview, decoded: dict = Depends(verify_firebase_token)):
    expert_id = _expert(decoded)
    _case_id(case_id)
    if not _ID.fullmatch(farmer_id) or not _ID.fullmatch(follow_up_id):
        raise HTTPException(404, "Follow-up not found")
    if not payload.note.strip():
        raise HTTPException(422, "Review note is required")
    if payload.next_follow_up_date and payload.next_follow_up_date <= _today_philippine():
        raise HTTPException(422, "Next follow-up date must be in the future")
    reviewed = store.review_follow_up(farmer_id, case_id, follow_up_id,
                                      payload.confirmed_outcome, payload.note.strip(), expert_id,
                                      payload.next_follow_up_date.isoformat() if payload.next_follow_up_date else None)
    if not reviewed:
        raise HTTPException(404, "Follow-up not found")
    return media_service.present_monitoring_follow_up(reviewed, expert=True)


@router.get("/monitoring/cases/{case_id}/follow-ups")
def my_follow_ups(case_id: str, decoded: dict = Depends(verify_firebase_token)):
    _case_id(case_id)
    farmer_id = _farmer(decoded)
    _require_case(farmer_id, case_id)
    return {"follow_ups": [media_service.present_monitoring_follow_up(item)
                           for item in store.list_follow_ups(farmer_id, case_id)]}


def _follow_up_decode_flag(image_format: str, pixels: int) -> int:
    """Use JPEG DCT reduction before allocating an inference raster."""
    if image_format != "JPEG":
        return cv2.IMREAD_COLOR
    if pixels > INFERENCE_DECODE_MAX_PIXELS * 16:
        return cv2.IMREAD_REDUCED_COLOR_8
    if pixels > INFERENCE_DECODE_MAX_PIXELS * 4:
        return cv2.IMREAD_REDUCED_COLOR_4
    if pixels > INFERENCE_DECODE_MAX_PIXELS:
        return cv2.IMREAD_REDUCED_COLOR_2
    return cv2.IMREAD_COLOR


def _apply_exif_orientation(image: np.ndarray, orientation: int) -> np.ndarray:
    """Orient the inference raster without changing the stored source bytes."""
    if orientation == 2:
        return cv2.flip(image, 1)
    if orientation == 3:
        return cv2.rotate(image, cv2.ROTATE_180)
    if orientation == 4:
        return cv2.flip(image, 0)
    if orientation == 5:
        return cv2.transpose(image)
    if orientation == 6:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    if orientation == 7:
        return cv2.flip(cv2.transpose(image), -1)
    if orientation == 8:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return image


def _decode_follow_up(contents: bytes, _declared_type: str | None):
    """Validate source bytes and decode a bounded raster for monitoring inference.

    Browser MIME labels are advisory: Safari and some Android camera apps may
    omit or mislabel an otherwise valid JPEG.  The decoded file signature is
    the authority, matching the initial disease-detection endpoint.
    """
    try:
        with Image.open(BytesIO(contents)) as image:
            image_format = image.format
            width, height = image.size
            pixels = width * height
            if image_format not in {"JPEG", "PNG", "MPO"}:
                raise ValueError("Unsupported image format. Upload a JPEG or PNG image")
            if pixels > MAX_IMAGE_UPLOAD_PIXELS:
                raise ValueError(
                    f"Image resolution is {width} x {height} ({pixels / 1_000_000:.2f} megapixels); "
                    f"the upload limit is {MAX_IMAGE_UPLOAD_PIXELS / 1_000_000:g} megapixels"
                )
            content_type = "image/png" if image_format == "PNG" else "image/jpeg"
            image.verify()
        # DJI MPO captures contain a primary JPEG plus a second frame. Match
        # initial detection: retain that original primary JPEG (including its
        # EXIF/GPS metadata) for inference and authenticated evidence storage.
        evidence_contents = (_mpo_primary_jpeg_bytes(contents)
                             if image_format == "MPO" else contents)
        decode_format = "JPEG" if image_format == "MPO" else image_format
        # Keep verify() directly after open; some Pillow decoders (notably
        # PNG) reject verification after metadata access. Reopening only reads
        # EXIF metadata and does not load the pixel raster.
        with Image.open(BytesIO(evidence_contents)) as image:
            orientation = int(image.getexif().get(274, 1))
        decoded_image = cv2.imdecode(
            np.frombuffer(evidence_contents, np.uint8),
            _follow_up_decode_flag(decode_format, pixels) | cv2.IMREAD_IGNORE_ORIENTATION,
        )
        if decoded_image is None:
            raise ValueError("Image file is corrupted or invalid")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(400, str(exc) or "Upload must be a valid image") from exc

    return _apply_exif_orientation(decoded_image, orientation), content_type, evidence_contents, {
        "width": height if orientation in {5, 6, 7, 8} else width,
        "height": width if orientation in {5, 6, 7, 8} else height,
    }


@router.post("/monitoring/cases/{case_id}/follow-ups", status_code=201)
async def upload_follow_up(case_id: str, file: UploadFile = File(...),
                           preliminary_status: str = Form("needs_review"),
                           follow_up_schedule_id: str | None = Form(None),
                           tree_reference_file: UploadFile | None = File(None),
                           decoded: dict = Depends(verify_firebase_token)):
    _case_id(case_id)
    farmer_id = _farmer(decoded)
    case = await run_in_threadpool(_require_case, farmer_id, case_id)
    allowance = await run_in_threadpool(store.follow_up_upload_allowance, farmer_id, case["tree_id"])
    if allowance["remaining"] == 0:
        raise HTTPException(409, store.FOLLOW_UP_LIMIT_MESSAGE)
    if preliminary_status not in {"needs_review", "improving", "no_visible_change", "worsening"}:
        raise HTTPException(422, "Invalid preliminary status")
    follow_up_schedule = None
    treatment_application_id = None
    if follow_up_schedule_id:
        if not _ID.fullmatch(follow_up_schedule_id):
            raise HTTPException(404, "Follow-up schedule not found")
        follow_up_schedule = await run_in_threadpool(store.get_follow_up_schedule, farmer_id, case_id, follow_up_schedule_id)
        if not follow_up_schedule:
            raise HTTPException(404, "Follow-up schedule not found")
        treatment_application_id = follow_up_schedule.get("treatment_application_id")

    # Validate every input before inference, quota consumption, or any uploads.
    tree_contents, tree_mime = (await read_reference_image(tree_reference_file)
                                if tree_reference_file is not None else (None, None))

    contents = await file.read(MAX_IMAGE_UPLOAD_BYTES + 1)
    if len(contents) > MAX_IMAGE_UPLOAD_BYTES:
        raise HTTPException(413, "Image upload is too large")
    if not contents:
        raise HTTPException(400, "Image upload is empty")
    decoded_image, content_type, evidence_contents, source_dimensions = await run_in_threadpool(
        _decode_follow_up, contents, file.content_type,
    )

    await limiter.rolling(f"image-minute:{farmer_id}", IMAGE_INFERENCE_PER_MINUTE, 60)
    await limiter.image_quota_24h(f"image-quota:{farmer_id}", IMAGE_INFERENCE_DAILY_LIMIT)
    return await run_in_threadpool(
        _process_follow_up, case, farmer_id, case_id, evidence_contents, file.filename,
        decoded_image, content_type, source_dimensions, tree_contents, tree_mime,
        tree_reference_file.filename if tree_reference_file is not None else "",
        preliminary_status, follow_up_schedule_id, treatment_application_id,
    )


def _process_follow_up(case, farmer_id, case_id, contents, filename, decoded_image,
                       content_type, source_dimensions, tree_contents, tree_mime, tree_filename,
                       preliminary_status, follow_up_schedule_id, treatment_application_id):
    """Keep evidence uploads and the commit/cleanup sequence together in one worker."""
    from model import detector  # noqa: PLC0415
    from drone_gps import DroneGPS  # noqa: PLC0415

    try:
        result = run_image_inference(
            detector, decoded_image, UPLOAD_DISPLAY_CONFIDENCE_THRESHOLD, annotate=False,
        )
    except InferenceUnavailable as exc:
        logger.error("Monitoring inference failed for %s/%s: %s", farmer_id, case_id, exc)
        if exc.category == "image_too_large":
            status_code, detail = 413, "The follow-up image is too large for disease analysis."
        elif exc.category in {"rate_limited", "cooldown", "request_in_progress"}:
            status_code, detail = 429, "Disease analysis is temporarily busy. Please try again shortly."
        else:
            status_code, detail = 503, "Disease analysis is temporarily unavailable. The follow-up was not saved."
        raise HTTPException(
            status_code,
            detail,
        ) from exc
    image_height, image_width = decoded_image.shape[:2]
    image_dimensions = source_dimensions
    predictions = _annotated_detections(result["detections"], (image_width, image_height))
    gps_reader = DroneGPS()
    gps = gps_reader.extract_gps_from_image(contents, filename or "")
    gps_data = ({"latitude": gps.latitude, "longitude": gps.longitude,
                 "accuracy": gps.accuracy, "source": gps.source} if gps else None)
    follow_up_id = uuid4().hex
    uploaded_evidence: list[dict] = []
    tree_reference = None

    def cleanup_new_evidence() -> None:
        # References are unique to this request; cleanup is exact-ID only.
        for reference in reversed(uploaded_evidence):
            delete_asset(reference)

    try:
        original_asset = upload_asset(contents, content_type, "monitoring-original")
        uploaded_evidence.append(original_asset)
        if tree_contents is not None:
            metadata = reference_metadata(case, uuid4().hex, farmer_id, tree_contents,
                                          tree_mime, tree_filename or "", follow_up_id)
            tree_asset = upload_asset(tree_contents, tree_mime, "whole-tree-reference")
            uploaded_evidence.append(tree_asset)
            tree_reference = {**metadata, "image_asset": tree_asset}
    except Exception as exc:
        cleanup_new_evidence()
        logger.exception("Could not upload follow-up evidence")
        raise HTTPException(503, "Follow-up evidence storage is temporarily unavailable") from exc

    record = FollowUpRecord(
        id=follow_up_id, case_id=case_id, farmer_id=farmer_id,
        tree_id=case["tree_id"], baseline_detection_id=case["baseline_detection_id"],
        treatment_record_id=case["treatment_record_id"], disease=case["disease"],
        follow_up_schedule_id=follow_up_schedule_id,
        treatment_application_id=treatment_application_id,
        created_at=_audit_timestamp(),
        filename=filename or "", predictions=predictions,
        image_dimensions=image_dimensions,
        annotation_format="xyxy-normalized-v1",
        image_url=f"/media/monitoring/{case_id}/follow-ups/{follow_up_id}/original",
        annotated_image_url=f"/media/monitoring/{case_id}/follow-ups/{follow_up_id}/annotated",
        original_storage_path=None,
        annotated_storage_path=None,
        image_assets={"original": original_asset},
        gps_data=gps_data, preliminary_status=preliminary_status,
        tree_reference=tree_reference,
        identity_baseline_reference_id=(case.get("tree_reference") or {}).get("id"),
    ).model_dump(mode="json")
    try:
        store.save_follow_up(farmer_id, case_id, record)
        return media_service.present_monitoring_follow_up(record)
    except store.FollowUpLimitReached as exc:
        cleanup_new_evidence()
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        # A failed Firestore commit can be ambiguous. Remove the new assets
        # only when a follow-up read succeeds and proves no record was stored.
        existing = None
        persistence_absent = False
        try:
            existing = store.get_follow_up(farmer_id, case_id, follow_up_id)
            persistence_absent = existing is None
        except Exception:
            logger.warning("Could not verify follow-up persistence after a write failure")
        if isinstance(existing, dict) and (
            existing.get("image_assets") == record.get("image_assets")
            and (existing.get("tree_reference") or {}).get("id")
            == (record.get("tree_reference") or {}).get("id")
        ):
            return media_service.present_monitoring_follow_up(record)
        if persistence_absent or isinstance(existing, dict):
            cleanup_new_evidence()
        logger.exception("Could not persist follow-up evidence")
        raise HTTPException(503, "Follow-up storage unavailable") from exc


@router.put("/expert/monitoring/cases/{farmer_id}/{case_id}/finish")
def finish_monitoring_case(farmer_id: str, case_id: str,
                           decoded: dict = Depends(verify_firebase_token)):
    expert_id = _expert(decoded)
    _case_id(case_id)
    if not _ID.fullmatch(farmer_id):
        raise HTTPException(404, "Monitoring case not found")
    updated = store.finish_case(farmer_id, case_id, expert_id)
    if not updated:
        raise HTTPException(404, "Monitoring case not found")
    return media_service.present_monitoring_case(updated)
