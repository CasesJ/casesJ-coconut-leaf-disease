"""
app/services/notification_service.py — Notification management service.

Thin orchestration over domain notification helpers. Adds Firebase RTDB
mutation operations (clear, mark-read) that are too stateful for domain layer.
"""
import json
import logging

from app.core.config import USER_NOTIFICATIONS_PATH
from app.domain.notification import get_user_notifications

logger = logging.getLogger(__name__)


def _get_db():
    from firebase_admin import db  # noqa: PLC0415
    return db


def _get_firestore():
    try:
        from firebase_admin import firestore  # noqa: PLC0415
        return firestore.client()
    except Exception as error:
        logger.warning("Firestore notification store unavailable: %s", error)
        return None


def clear_notifications(user_id: str) -> None:
    """Delete all notifications for a user from RTDB and local file."""
    firestore_client = _get_firestore()
    if firestore_client:
        try:
            batch = firestore_client.batch()
            docs = firestore_client.collection("users").document(user_id).collection("notifications").stream()
            for doc in docs:
                batch.delete(doc.reference)
            batch.commit()
            return
        except Exception as error:
            logger.warning("Firestore notification clear failed: %s", error)
    try:
        _get_db().reference(f"users/{user_id}/notifications").delete()
    except Exception as error:
        logger.warning("RTDB notification clear failed: %s", error)
    try:
        stored = (
            json.loads(USER_NOTIFICATIONS_PATH.read_text(encoding="utf-8"))
            if USER_NOTIFICATIONS_PATH.exists()
            else {}
        )
        stored.pop(user_id, None)
        USER_NOTIFICATIONS_PATH.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    except Exception as error:
        logger.warning("Local notification clear failed: %s", error)


def mark_notifications_read(user_id: str) -> None:
    """Mark all notifications as read in RTDB and local file."""
    notices = get_user_notifications(user_id)
    for notice in notices:
        notice["read"] = True
    firestore_client = _get_firestore()
    if firestore_client:
        try:
            batch = firestore_client.batch()
            collection = firestore_client.collection("users").document(user_id).collection("notifications")
            for notice in notices:
                if notice.get("id"):
                    batch.set(collection.document(str(notice["id"])), notice)
            batch.commit()
            return
        except Exception as error:
            logger.warning("Firestore notification mark-read failed: %s", error)
    try:
        _get_db().reference(f"users/{user_id}/notifications").set(
            {notice["id"]: notice for notice in notices if notice.get("id")}
        )
    except Exception as error:
        logger.warning("RTDB notification mark-read failed: %s", error)
    try:
        stored = (
            json.loads(USER_NOTIFICATIONS_PATH.read_text(encoding="utf-8"))
            if USER_NOTIFICATIONS_PATH.exists()
            else {}
        )
        stored[user_id] = notices
        USER_NOTIFICATIONS_PATH.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    except Exception as error:
        logger.warning("Local notification mark-read failed: %s", error)
