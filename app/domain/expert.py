"""
app/domain/expert.py — Expert identity checks and audit-log helpers.

Pure functions: no HTTP, no database calls.
"""
import json
import logging
from datetime import datetime, timezone

from app.core.config import (
    EXPERT_ACCOUNT_EMAIL,
    EXPERT_ACCOUNT_EMAILS,
    EXPERT_ACCOUNT_UID,
    EXPERT_ACCOUNT_UIDS,
    EXPERT_AUDIT_LOG_PATH,
)

logger = logging.getLogger(__name__)


def is_expert_identity(decoded: dict | None) -> bool:
    """Return True when the Firebase identity has expert privileges."""
    if not decoded:
        return False
    role = str(decoded.get("role") or decoded.get("custom_role") or "").strip().lower()
    if role == "expert":
        return True
    email = str(decoded.get("email") or "").strip().lower()
    uid = str(decoded.get("uid") or "").strip()
    if EXPERT_ACCOUNT_EMAIL and email == EXPERT_ACCOUNT_EMAIL:
        return True
    if EXPERT_ACCOUNT_UID and uid == EXPERT_ACCOUNT_UID:
        return True
    if email and email in EXPERT_ACCOUNT_EMAILS:
        return True
    if uid and uid in EXPERT_ACCOUNT_UIDS:
        return True
    return False


def normalize_disease_key(disease_name: str) -> str:
    """Create a stable dictionary key for recommendation overrides."""
    return str(disease_name or "").strip().lower().replace(" ", "_")


def append_expert_audit_event(
    action: str,
    actor: dict,
    target: dict | None = None,
    details: dict | None = None,
) -> None:
    """Append an expert action to the local JSONL audit log."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "actor_email": actor.get("email") if actor else None,
        "actor_uid": actor.get("uid") if actor else None,
        "target": target or {},
        "details": details or {},
    }
    try:
        with open(EXPERT_AUDIT_LOG_PATH, "a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=True) + "\n")
    except Exception as error:
        logger.warning("Could not write expert audit entry: %s", error)


def read_expert_audit_events(limit: int = 100) -> list[dict]:
    """Return the latest expert actions from the local JSONL audit log."""
    if not EXPERT_AUDIT_LOG_PATH.exists():
        return []
    events: list[dict] = []
    try:
        with open(EXPERT_AUDIT_LOG_PATH, "r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except Exception:
                    continue
    except Exception as error:
        logger.warning("Could not read expert audit log: %s", error)
        return []
    events.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
    return events[:limit]
