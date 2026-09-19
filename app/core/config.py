"""
app/core/config.py — All application constants and environment-variable reads.

This module is the single source of truth for every configuration value.
No other module should call os.getenv() or declare path constants directly.
"""
import os
from pathlib import Path

from dotenv import load_dotenv


# Load local development settings before reading configuration.  Hosted
# environments provide the same values through their secret manager.
load_dotenv()


def _required_env(name: str) -> str:
    """Return a required configuration value or stop startup with a clear error."""
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


# Browser origins allowed to call the API. Keep local URLs for development;
# add the deployed frontend URL with CORS_ALLOWED_ORIGINS as a comma-separated
# environment variable, for example: https://app.example.com.
_DEFAULT_CORS_ORIGINS = ("http://localhost:8000", "http://127.0.0.1:8000")
_configured_cors_origins = os.getenv("CORS_ALLOWED_ORIGINS", "")
CORS_ALLOWED_ORIGINS: list[str] = [
    origin.strip().rstrip("/")
    for origin in _configured_cors_origins.split(",")
    if origin.strip()
] or list(_DEFAULT_CORS_ORIGINS)

# ── Confidence thresholds ──────────────────────────────────────────────────────
# Show all detections >= 5% to the farmer; still record and save them.
UPLOAD_DISPLAY_CONFIDENCE_THRESHOLD: float = 0.05
UPLOAD_RECORD_CONFIDENCE_THRESHOLD: float = 0.05
# Uploads whose primary detection is strictly above this value are automatically
# verified and do not need to enter the expert-review queue.
AUTO_VERIFICATION_CONFIDENCE_THRESHOLD: float = 0.50

# Default farm location shown at the center of the disease map (Panabo, Davao
# del Norte).  Uploads without usable GPS metadata are recorded here so they
# appear on the farm map instead of at a separate generic fallback location.
DEFAULT_FARM_LATITUDE: float = 7.3137591
DEFAULT_FARM_LONGITUDE: float = 125.6659531
DEFAULT_FARM_GPS_ACCURACY_METERS: float = 5.0

# ── Verification status labels ─────────────────────────────────────────────────
PENDING_VERIFICATION_STATUS = "pending_verification"
VERIFIED_STATUS = "verified"

# ── Expert account credentials (from environment / .env) ──────────────────────
EXPERT_ACCOUNT_EMAIL: str = _required_env("EXPERT_ACCOUNT_EMAIL").lower()
EXPERT_ACCOUNT_PASSWORD: str = _required_env("EXPERT_ACCOUNT_PASSWORD")
EXPERT_ACCOUNT_UID: str = os.getenv("EXPERT_ACCOUNT_UID", "").strip()

EXPERT_ACCOUNT_EMAILS: set[str] = {
    email.strip().lower()
    for email in os.getenv("EXPERT_ACCOUNT_EMAILS", "").split(",")
    if email.strip()
}
EXPERT_ACCOUNT_EMAILS.add(EXPERT_ACCOUNT_EMAIL)

EXPERT_ACCOUNT_UIDS: set[str] = {
    uid.strip()
    for uid in os.getenv("EXPERT_ACCOUNT_UIDS", "").split(",")
    if uid.strip()
}

# ── Disease catalogue ──────────────────────────────────────────────────────────
EXPERT_DISEASE_OPTIONS: list[str] = [
    "Caterpillars",
    "Cercospora",
    "Drying of Leaflets",
    "Healthy",
    "Pestalotiopsis",
    "Bud Root",
]

# ── Filesystem paths ───────────────────────────────────────────────────────────
# All paths are relative to the project root (one level above app/).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

UPLOAD_IMAGE_DIR: Path = _PROJECT_ROOT / "static" / "uploads"
ANNOTATED_IMAGE_DIR: Path = _PROJECT_ROOT / "static" / "annotated_uploads"
EXPERT_RECOMMENDATIONS_PATH: Path = _PROJECT_ROOT / "expert_recommendations.json"
EXPERT_AUDIT_LOG_PATH: Path = _PROJECT_ROOT / "expert_audit_log.jsonl"
USER_NOTIFICATIONS_PATH: Path = _PROJECT_ROOT / "user_notifications.json"

# Ensure upload directories exist at import time.
UPLOAD_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
ANNOTATED_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
