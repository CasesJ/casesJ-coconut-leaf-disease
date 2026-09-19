"""One-time migration of local persistence into Firestore and Cloud Storage.

Run only after taking a backup and configuring GOOGLE_APPLICATION_CREDENTIALS:
    python tools/migrate_local_data_to_firebase.py
"""
from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import firebase_config  # noqa: E402,F401 - initialise Firebase Admin SDK
from firebase_admin import firestore  # noqa: E402

from app.core.config import (  # noqa: E402
    ANNOTATED_IMAGE_DIR,
    EXPERT_AUDIT_LOG_PATH,
    EXPERT_RECOMMENDATIONS_PATH,
    UPLOAD_IMAGE_DIR,
    USER_NOTIFICATIONS_PATH,
)
from app.infrastructure.firebase.cloud_storage import upload_image  # noqa: E402
from app.infrastructure.storage import get_local_storage  # noqa: E402


def migrate_detections(client) -> int:
    migrated = 0
    for record in get_local_storage().get_all_detections(limit=100_000):
        record = dict(record)
        record_id = str(record.get("id") or "").strip()
        user_id = str(record.get("user_id") or "").strip()
        if not record_id or not user_id:
            continue
        original_path = UPLOAD_IMAGE_DIR / f"{record_id}.jpg"
        annotated_path = ANNOTATED_IMAGE_DIR / f"{record_id}.jpg"
        if original_path.exists():
            record["image_url"] = upload_image(record_id, "original", original_path.read_bytes()) or record.get("image_url", "")
        if annotated_path.exists():
            record["annotated_image_url"] = upload_image(record_id, "annotated", annotated_path.read_bytes()) or record.get("annotated_image_url", "")
        client.collection("users").document(user_id).collection("detections").document(record_id).set(record, merge=True)
        migrated += 1
    return migrated


def migrate_json_state(client) -> None:
    if EXPERT_RECOMMENDATIONS_PATH.exists():
        data = json.loads(EXPERT_RECOMMENDATIONS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            client.collection("app_settings").document("expert_recommendations").set({"overrides": data})

    if USER_NOTIFICATIONS_PATH.exists():
        notices_by_user = json.loads(USER_NOTIFICATIONS_PATH.read_text(encoding="utf-8"))
        if isinstance(notices_by_user, dict):
            for user_id, notices in notices_by_user.items():
                for notice in notices if isinstance(notices, list) else []:
                    if isinstance(notice, dict) and notice.get("id"):
                        client.collection("users").document(str(user_id)).collection("notifications").document(str(notice["id"])).set(notice)

    if EXPERT_AUDIT_LOG_PATH.exists():
        for line in EXPERT_AUDIT_LOG_PATH.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                event_id = hashlib.sha256(
                    json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                client.collection("expert_audit_events").document(event_id).set(event)


def main() -> int:
    client = firestore.client()
    count = migrate_detections(client)
    migrate_json_state(client)
    print(f"Migrated {count} detection records and local JSON state to Firebase.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
