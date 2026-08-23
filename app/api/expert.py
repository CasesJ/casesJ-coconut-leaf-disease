"""
app/api/expert.py — Expert-only endpoints for record review and recommendations.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import (
    EXPERT_DISEASE_OPTIONS,
    PENDING_VERIFICATION_STATUS,
    VERIFIED_STATUS,
)
from app.core.security import verify_firebase_token
from app.domain.detection import _extract_primary_disease
from app.domain.expert import (
    append_expert_audit_event,
    is_expert_identity,
    normalize_disease_key,
    read_expert_audit_events,
)
from app.domain.notification import create_user_notification
from app.domain.recommendation import (
    build_recommendation_response,
    load_expert_recommendations,
    save_expert_recommendations,
)
from app.schemas.expert import ExpertRecommendationUpdate, VerificationUpdate
from app.services.detection_service import (
    find_record_for_user,
    get_all_user_records,
    update_record_everywhere,
)

router = APIRouter(prefix="/expert", tags=["expert"])
logger = logging.getLogger(__name__)


def _require_expert(decoded: dict) -> None:
    if not is_expert_identity(decoded):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Expert access required")


@router.get("/records")
async def get_expert_records(
    status: str = "pending",
    decoded: dict = Depends(verify_firebase_token),
):
    """Get all detection records across all farmers for expert review."""
    _require_expert(decoded)
    all_records = get_all_user_records()
    pending_count = sum(1 for r in all_records if r.get("verification_status") == PENDING_VERIFICATION_STATUS)
    verified_count = sum(1 for r in all_records if r.get("verification_status") == VERIFIED_STATUS)
    status_value = str(status or "pending").strip().lower()
    if status_value == "all":
        filtered = all_records
    elif status_value == "verified":
        filtered = [r for r in all_records if r.get("verification_status") == VERIFIED_STATUS]
    else:
        filtered = [r for r in all_records if r.get("verification_status") == PENDING_VERIFICATION_STATUS]
    return {
        "role": "expert", "email": decoded.get("email"),
        "total_records": len(all_records), "pending_records": pending_count,
        "verified_records": verified_count, "filter": status_value, "records": filtered,
    }


@router.get("/pending-records")
async def get_pending_records(decoded: dict = Depends(verify_firebase_token)):
    """Get only pending records for expert verification."""
    _require_expert(decoded)
    records = [r for r in get_all_user_records() if r.get("verification_status") == PENDING_VERIFICATION_STATUS]
    return {"role": "expert", "total_records": len(records), "records": records}


@router.post("/records/{user_id}/{record_id}/verify")
async def verify_expert_record(
    user_id: str,
    record_id: str,
    payload: VerificationUpdate,
    decoded: dict = Depends(verify_firebase_token),
):
    """Mark a pending upload as verified (or unverified) by an expert."""
    _require_expert(decoded)

    desired_status = str(payload.status or VERIFIED_STATUS).strip().lower()
    if desired_status not in {PENDING_VERIFICATION_STATUS, VERIFIED_STATUS}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid status")

    record = find_record_for_user(user_id, record_id)
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

    if record.get("primaryDisease"):
        primary = {
            "class": str(record.get("primaryDisease") or ""),
            "confidence": float(record.get("primaryConfidence") or record.get("primary_confidence") or 0),
        }
    elif detections:
        primary = max(
            ({"class": str(d.get("class") or ""), "confidence": float(d.get("confidence") or 0)} for d in detections if isinstance(d, dict)),
            key=lambda item: item.get("confidence", 0),
            default=None,
        )
    else:
        primary = None

    if not primary or not primary.get("class"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to determine record disease")

    try:
        recommendation_snapshot = build_recommendation_response(
            disease_name=primary["class"], confidence=primary["confidence"],
        )
    except Exception as error:
        logger.warning("Could not build recommendation snapshot for %s: %s", record_id, error)
        recommendation_snapshot = {
            "disease": primary["class"],
            "confidence_percent": round(float(primary["confidence"]) * 100, 2),
            "model_used": "Unknown",
            "recommendations": {"fertilizer": "", "treatment": "", "prevention": []},
            "location": {}, "note": "", "source": "default",
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
        updates.update({"verified_by": None, "verified_by_uid": None, "verified_at": None,
                        "recommendation_snapshot": None, "recommendation_source": None})

    updated = update_record_everywhere(user_id, record_id, updates)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")

    if desired_status == VERIFIED_STATUS:
        create_user_notification(
            user_id, record_id,
            "Upload verified by an expert",
            "Your uploaded image has been reviewed and verified. View your Detection Records for the result.",
        )

    append_expert_audit_event(
        action="verify_record", actor=decoded,
        target={"user_id": user_id, "record_id": record_id},
        details={"verification_status": desired_status},
    )
    return {"message": "Record updated successfully", "user_id": user_id,
            "record_id": record_id, "verification_status": desired_status}


@router.get("/recommendations")
async def get_expert_recommendations(decoded: dict = Depends(verify_firebase_token)):
    """List expert-edited recommendation overrides."""
    _require_expert(decoded)
    overrides = load_expert_recommendations()
    return {"overrides": overrides, "count": len(overrides)}


@router.get("/diseases")
async def get_expert_diseases(decoded: dict = Depends(verify_firebase_token)):
    """Return the supported disease list for the expert editor."""
    _require_expert(decoded)
    return {"diseases": EXPERT_DISEASE_OPTIONS, "count": len(EXPERT_DISEASE_OPTIONS)}


@router.get("/recommendations/{disease}")
async def get_expert_recommendation(disease: str, decoded: dict = Depends(verify_firebase_token)):
    """Fetch a single disease recommendation override."""
    _require_expert(decoded)
    overrides = load_expert_recommendations()
    key = normalize_disease_key(disease)
    return {"disease": disease, "override": overrides.get(key)}


@router.put("/recommendations/{disease}")
async def update_expert_recommendation(
    disease: str,
    payload: ExpertRecommendationUpdate,
    decoded: dict = Depends(verify_firebase_token),
):
    """Save an expert recommendation for exactly one uploaded record."""
    _require_expert(decoded)

    target_user_id = str(payload.target_user_id or "").strip()
    target_record_id = str(payload.target_record_id or "").strip()
    if not target_user_id or not target_record_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Select a pending upload before saving a recommendation")

    target_record = find_record_for_user(target_user_id, target_record_id)
    if not target_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Selected record not found")

    direct_record_synced = False
    try:
        target_primary_disease, target_primary_confidence = _extract_primary_disease(target_record)
        expert_snapshot = {
            "disease": payload.disease or target_primary_disease or disease,
            "confidence_percent": round(float(target_primary_confidence or 0) * 100, 2),
            "model_used": "Expert Override",
            "recommendations": {"fertilizer": payload.fertilizer, "treatment": payload.treatment, "prevention": payload.prevention},
            "location": {
                "lat": target_record.get("lat"), "lng": target_record.get("lng"),
                "source": target_record.get("gps_source") or target_record.get("location_source") or "",
            },
            "note": payload.note or "", "source": "expert_override",
            "recommendation_scope": "record",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        direct_record_synced = update_record_everywhere(target_user_id, target_record_id, {
            "verification_status": VERIFIED_STATUS,
            "verified_by": decoded.get("email"),
            "verified_by_uid": decoded.get("uid"),
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "recommendation_snapshot": expert_snapshot,
            "recommendation_source": "expert_override",
        })
    except Exception as error:
        logger.warning("Could not save expert recommendation for %s: %s", target_record_id, error)

    if direct_record_synced:
        create_user_notification(
            target_user_id, target_record_id,
            "Upload verified by an expert",
            "Your uploaded image was verified and an expert recommendation is now available in Detection Records.",
        )

    append_expert_audit_event(
        action="update_recommendation", actor=decoded,
        target={"disease": disease, "user_id": payload.target_user_id, "record_id": payload.target_record_id},
        details={"verification_status": VERIFIED_STATUS if direct_record_synced else "pending",
                 "active": payload.active, "prevention_count": len(payload.prevention or []),
                 "synced_records": 0, "direct_record_synced": direct_record_synced},
    )
    return {"message": "Recommendation saved for the selected record", "disease": disease,
            "synced_records": 0, "direct_record_synced": direct_record_synced}


@router.get("/audit-log")
async def get_expert_audit_log(limit: int = 100, decoded: dict = Depends(verify_firebase_token)):
    """Get the latest expert audit events."""
    _require_expert(decoded)
    events = read_expert_audit_events(limit=limit)
    for event in events:
        target = event.get("target") or {}
        uid = str(target.get("user_id") or "").strip()
        rid = str(target.get("record_id") or "").strip()
        if not uid or not rid:
            continue
        record = find_record_for_user(uid, rid)
        if not record:
            continue
        event["upload"] = {
            "user_id": uid, "email": str(record.get("email") or ""),
            "filename": str(record.get("filename") or record.get("image_path") or ""),
            "image_url": str(record.get("image_url") or ""),
            "annotated_image_url": str(record.get("annotated_image_url") or ""),
        }
    return {"events": events, "limit": limit}
