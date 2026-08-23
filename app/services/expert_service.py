"""
app/services/expert_service.py — Expert verification and recommendation sync orchestration.
"""
import logging
from datetime import datetime, timezone

from app.core.config import PENDING_VERIFICATION_STATUS, VERIFIED_STATUS
from app.domain.detection import _extract_primary_disease, normalize_verification_status
from app.domain.expert import normalize_disease_key
from app.domain.recommendation import build_recommendation_response
from app.services.detection_service import get_all_user_records, update_record_everywhere

logger = logging.getLogger(__name__)


def sync_recommendation_snapshot_for_disease(disease_name: str) -> int:
    """Update saved recommendation snapshots for all verified records of a disease."""
    disease_key = normalize_disease_key(disease_name)
    if not disease_key:
        return 0

    try:
        recommendation = build_recommendation_response(disease_name=disease_name, confidence=0.85)
    except Exception as error:
        logger.warning("Could not build synced recommendation for %s: %s", disease_name, error)
        return 0

    try:
        records = get_all_user_records(limit=5000)
    except Exception as error:
        logger.warning("Could not collect records for recommendation sync: %s", error)
        return 0

    synced = 0
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
        if update_record_everywhere(user_id, record_id, updates):
            synced += 1

    return synced
