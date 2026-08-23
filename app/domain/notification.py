"""
app/domain/notification.py — User notification persistence helpers.

Falls back gracefully between Firebase RTDB and local JSON file storage.
"""
import json
import logging
import uuid
from datetime import datetime, timezone

from app.core.config import USER_NOTIFICATIONS_PATH

logger = logging.getLogger(__name__)


def create_user_notification(user_id: str, record_id: str, title: str, message: str) -> None:
    """Persist an in-app alert for a record owner across app sessions."""
    if not user_id:
        return
    notice = {
        "id": str(uuid.uuid4()),
        "record_id": record_id,
        "title": title,
        "message": message,
        "type": "verification",
        "read": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        from firebase_admin import db  # noqa: PLC0415
        db.reference(f"users/{user_id}/notifications/{notice['id']}").set(notice)
    except Exception as error:
        logger.warning("RTDB notification write failed: %s", error)
    try:
        stored = (
            json.loads(USER_NOTIFICATIONS_PATH.read_text(encoding="utf-8"))
            if USER_NOTIFICATIONS_PATH.exists()
            else {}
        )
        items = stored.get(user_id, [])
        items.insert(0, notice)
        stored[user_id] = items[:100]
        USER_NOTIFICATIONS_PATH.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    except Exception as error:
        logger.warning("Local notification write failed: %s", error)


def get_user_notifications(user_id: str) -> list[dict]:
    """Read notifications, preferring Firebase RTDB but retaining offline support."""
    try:
        from firebase_admin import db  # noqa: PLC0415
        remote = db.reference(f"users/{user_id}/notifications").get() or {}
        if isinstance(remote, dict) and remote:
            return sorted(
                remote.values(),
                key=lambda item: item.get("created_at", ""),
                reverse=True,
            )
    except Exception as error:
        logger.warning("RTDB notification read failed: %s", error)
    try:
        stored = (
            json.loads(USER_NOTIFICATIONS_PATH.read_text(encoding="utf-8"))
            if USER_NOTIFICATIONS_PATH.exists()
            else {}
        )
        return stored.get(user_id, [])
    except Exception as error:
        logger.warning("Local notification read failed: %s", error)
        return []
