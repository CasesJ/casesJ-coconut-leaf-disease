"""Persistence for each farmer's initial metadata-derived farm centre."""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _valid(latitude: float, longitude: float) -> bool:
    return -90 <= latitude <= 90 and -180 <= longitude <= 180


def set_initial_farm_center(user_id: str, latitude: float, longitude: float, source: str) -> dict | None:
    """Atomically set the centre once; subsequent uploads cannot move it."""
    if not user_id or not _valid(latitude, longitude):
        return None
    proposed = {"latitude": float(latitude), "longitude": float(longitude),
                "source": source or "exif", "established_at": datetime.now(timezone.utc).isoformat()}
    try:
        from firebase_admin import db  # noqa: PLC0415
        saved = db.reference(f"users/{user_id}/farm_center").transaction(
            lambda current: current if isinstance(current, dict) else proposed
        )
        return saved if isinstance(saved, dict) else proposed
    except Exception as error:
        logger.warning("Unable to establish farm centre for %s: %s", user_id, error)
        return None


def get_farm_center(user_id: str) -> dict | None:
    try:
        from firebase_admin import db  # noqa: PLC0415
        center = db.reference(f"users/{user_id}/farm_center").get()
        if not isinstance(center, dict):
            return None
        latitude, longitude = float(center.get("latitude")), float(center.get("longitude"))
        if not _valid(latitude, longitude):
            return None
        return {**center, "latitude": latitude, "longitude": longitude}
    except Exception as error:
        logger.warning("Unable to read farm centre for %s: %s", user_id, error)
        return None
