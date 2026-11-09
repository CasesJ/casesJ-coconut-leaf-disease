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


def _positive_int_env(name: str, default: int) -> int:
    value = os.getenv(name, str(default)).strip()
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if parsed < 1:
        raise RuntimeError(f"{name} must be a positive integer")
    return parsed


def _bool_env(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


# Abuse controls. Image quotas use a user-specific 24-hour window starting at
# their first upload, evaluated in Philippine time (Asia/Manila).
IMAGE_INFERENCE_DAILY_LIMIT = _positive_int_env("IMAGE_INFERENCE_DAILY_LIMIT", 100)
IMAGE_INFERENCE_PER_MINUTE = _positive_int_env("IMAGE_INFERENCE_PER_MINUTE", 10)
EXPORTS_PER_MINUTE = _positive_int_env("EXPORTS_PER_MINUTE", 5)
AUTH_TOKEN_CHECKS_PER_MINUTE = _positive_int_env("AUTH_TOKEN_CHECKS_PER_MINUTE", 10)
PASSWORD_RECOVERY_ATTEMPTS_PER_MINUTE = _positive_int_env("PASSWORD_RECOVERY_ATTEMPTS_PER_MINUTE", 5)
PASSWORD_RECOVERY_ATTEMPTS_PER_HOUR = _positive_int_env("PASSWORD_RECOVERY_ATTEMPTS_PER_HOUR", 10)
WS_CONNECTIONS_PER_USER = _positive_int_env("WS_CONNECTIONS_PER_USER", 2)
WS_FRAMES_PER_SECOND = _positive_int_env("WS_FRAMES_PER_SECOND", 10)
WS_MAX_FRAME_BYTES = _positive_int_env("WS_MAX_FRAME_BYTES", 3_000_000)
# A full DJI upload briefly holds its compressed source, a decoded BGR image,
# OpenVINO input tensors, and (when necessary) a storage copy.  Keep only one
# such pipeline active in a small Render worker; normal API reads remain free.
IMAGE_PIPELINE_MAX_CONCURRENCY = _positive_int_env("IMAGE_PIPELINE_MAX_CONCURRENCY", 1)
IMAGE_PIPELINE_QUEUE_SECONDS = _positive_int_env("IMAGE_PIPELINE_QUEUE_SECONDS", 10)
# These limits bound the largest decoded BGR image held during inference.  A
# 160 MP photograph alone needs roughly 480 MB before model, preview, and
# storage buffers are considered, which is not viable on a small web instance.
# DJI still images up to 50 MP remain supported; deployments with more RAM may
# explicitly raise either value.
MAX_IMAGE_UPLOAD_BYTES = _positive_int_env("MAX_IMAGE_UPLOAD_BYTES", 50_000_000)
MAX_IMAGE_UPLOAD_PIXELS = _positive_int_env("MAX_IMAGE_UPLOAD_PIXELS", 50_000_000)
# JPEG cameras commonly produce much larger source rasters than the detector's
# compact model input. OpenCV can decode JPEG DCT layers at a lower resolution
# before allocating the BGR array. Coordinates are mapped back to the original
# raster after inference; the source bytes and EXIF remain untouched.
INFERENCE_DECODE_MAX_PIXELS = _positive_int_env("INFERENCE_DECODE_MAX_PIXELS", 12_000_000)
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "").strip()
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY", "").strip()
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "").strip()
CLOUDINARY_READ_TIMEOUT_SECONDS = _positive_int_env("CLOUDINARY_READ_TIMEOUT_SECONDS", 20)
CLOUDINARY_UPLOAD_TIMEOUT_SECONDS = _positive_int_env("CLOUDINARY_UPLOAD_TIMEOUT_SECONDS", 90)
CLOUDINARY_POOL_MAXSIZE = _positive_int_env("CLOUDINARY_POOL_MAXSIZE", 8)
DETECTION_STORAGE_MAX_BYTES = _positive_int_env("DETECTION_STORAGE_MAX_BYTES", 8_000_000)
# Storage derivatives are deliberately smaller than the original DJI evidence
# used for GPS extraction and inference. JPEG DCT reduction keeps this raster
# bounded before Pillow allocates it on small Render instances. Set the first
# value to the exact account limit shown in Cloudinary Console if it differs.
CLOUDINARY_MAX_IMAGE_PIXELS = _positive_int_env("CLOUDINARY_MAX_IMAGE_PIXELS", 25_000_000)
DETECTION_STORAGE_MAX_PIXELS = _positive_int_env("DETECTION_STORAGE_MAX_PIXELS", 12_000_000)
FARM_CENTER_TIMEOUT_SECONDS = _positive_int_env("FARM_CENTER_TIMEOUT_SECONDS", 3)
FARM_CENTER_CACHE_TTL_SECONDS = _positive_int_env("FARM_CENTER_CACHE_TTL_SECONDS", 300)
TREE_IDENTITY_GPS_TOLERANCE_METERS = _positive_int_env("TREE_IDENTITY_GPS_TOLERANCE_METERS", 25)
TRUST_PROXY_HEADERS = _bool_env("TRUST_PROXY_HEADERS")

# Inference backend. Render must opt in explicitly; local development keeps the
# established Ultralytics/PyTorch path unless this value is changed.
# The hosted workflow is the production path.  Unlike the local backends it
# does not load PyTorch or OpenVINO into a 512 MB Render worker.  Local modes
# remain explicit development fallbacks; they are never selected accidentally.
INFERENCE_BACKEND = os.getenv("INFERENCE_BACKEND", "roboflow").strip().lower()
if INFERENCE_BACKEND not in {"roboflow", "ultralytics", "openvino"}:
    raise RuntimeError("INFERENCE_BACKEND must be 'roboflow', 'ultralytics' or 'openvino'")
ROBOFLOW_API_URL = os.getenv("ROBOFLOW_API_URL", "https://serverless.roboflow.com").rstrip("/")
ROBOFLOW_API_KEY = os.getenv("ROBOFLOW_API_KEY", "").strip()
ROBOFLOW_WORKSPACE = os.getenv("ROBOFLOW_WORKSPACE", "").strip()
ROBOFLOW_WORKFLOW_ID = os.getenv("ROBOFLOW_WORKFLOW_ID", "").strip()
ROBOFLOW_IMAGE_INPUT_KEY = os.getenv("ROBOFLOW_IMAGE_INPUT_KEY", "image").strip() or "image"
ROBOFLOW_TIMEOUT_SECONDS = _positive_int_env("ROBOFLOW_TIMEOUT_SECONDS", 45)

# ── Confidence thresholds ──────────────────────────────────────────────────────
# Show all detections >= 5% to the farmer; still record and save them.
UPLOAD_DISPLAY_CONFIDENCE_THRESHOLD: float = 0.05
UPLOAD_RECORD_CONFIDENCE_THRESHOLD: float = 0.05
# Confidence affects how results are displayed and prioritised, but never
# verifies an upload. Every upload requires an expert review.

# Default farm location shown at the center of the disease map (Catipunan,
# Panabo City, Davao del Norte). It is a map viewport only: uploads without
# usable embedded GPS metadata must remain unmapped.
DEFAULT_FARM_LATITUDE: float = 7.30158
DEFAULT_FARM_LONGITUDE: float = 125.63099
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
    "Drying of Leaflets",
    "Healthy",
    "Grey Leaf Spot",
    "Bud Rot",
    "Stem Bleeding",
]

# ── Filesystem paths ───────────────────────────────────────────────────────────
# All paths are relative to the project root (one level above app/).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OPENVINO_MODEL_DIR: Path = Path(os.getenv(
    "OPENVINO_MODEL_DIR", str(_PROJECT_ROOT / "models" / "yolo26s_openvino_model")
)).expanduser()

UPLOAD_IMAGE_DIR: Path = _PROJECT_ROOT / "static" / "uploads"
ANNOTATED_IMAGE_DIR: Path = _PROJECT_ROOT / "static" / "annotated_uploads"
EXPERT_RECOMMENDATIONS_PATH: Path = _PROJECT_ROOT / "expert_recommendations.json"
EXPERT_AUDIT_LOG_PATH: Path = _PROJECT_ROOT / "expert_audit_log.jsonl"
USER_NOTIFICATIONS_PATH: Path = _PROJECT_ROOT / "user_notifications.json"

# Ensure upload directories exist at import time.
UPLOAD_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
ANNOTATED_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
