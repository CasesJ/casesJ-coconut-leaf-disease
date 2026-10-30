"""Persistence for each farmer's initial metadata-derived farm centre."""
import logging
import time
import uuid
from datetime import datetime, timezone
from threading import RLock

from requests.adapters import HTTPAdapter

from app.core import config

logger = logging.getLogger(__name__)
_CACHE_LOCK = RLock()
_CACHE: dict[str, tuple[float, dict]] = {}


def _valid(latitude: float, longitude: float) -> bool:
    return -90 <= latitude <= 90 and -180 <= longitude <= 180


def _normalize(center: object) -> dict | None:
    if not isinstance(center, dict):
        return None
    try:
        latitude, longitude = float(center.get("latitude")), float(center.get("longitude"))
    except (TypeError, ValueError, OverflowError):
        return None
    if not _valid(latitude, longitude):
        return None
    return {**center, "latitude": latitude, "longitude": longitude}


def _cached(user_id: str) -> dict | None:
    with _CACHE_LOCK:
        entry = _CACHE.get(user_id)
        if entry and entry[0] > time.monotonic():
            return dict(entry[1])
        if entry:
            _CACHE.pop(user_id, None)
    return None


def _remember(user_id: str, center: dict) -> dict:
    normalized = _normalize(center)
    if normalized is None:
        raise ValueError("Invalid farm centre")
    with _CACHE_LOCK:
        _CACHE[user_id] = (
            time.monotonic() + config.FARM_CENTER_CACHE_TTL_SECONDS,
            dict(normalized),
        )
    return normalized


def _bounded_reference(path: str):
    """Create an RTDB reference with a short timeout and no HTTP retries."""
    from firebase_admin import db  # noqa: PLC0415

    default = db.reference(path)
    client = default._client  # Firebase Admin has no public per-reference timeout API.
    bounded = db._Client(
        credential=client.credential,
        base_url=client.base_url,
        timeout=config.FARM_CENTER_TIMEOUT_SECONDS,
        params=client.params,
    )
    adapter = HTTPAdapter(max_retries=0, pool_connections=2, pool_maxsize=4)
    bounded.session.mount("http://", adapter)
    bounded.session.mount("https://", adapter)
    return db.Reference(client=bounded, path=path)


def set_initial_farm_center(user_id: str, latitude: float, longitude: float, source: str,
                            operation_id: str = "unknown") -> dict | None:
    """Set the centre once using one bounded ETag read and conditional write."""
    if operation_id == "unknown":
        operation_id = uuid.uuid4().hex[:12]
    if not user_id or not _valid(latitude, longitude):
        return None
    cached = _cached(user_id)
    if cached is not None:
        return cached
    proposed = {"latitude": float(latitude), "longitude": float(longitude),
                "source": source or "exif", "established_at": datetime.now(timezone.utc).isoformat()}
    started = time.perf_counter()
    try:
        reference = _bounded_reference(f"users/{user_id}/farm_center")
        current, etag = reference.get(etag=True)
        existing = _normalize(current)
        if existing is not None:
            return _remember(user_id, existing)
        committed, latest, _ = reference.set_if_unchanged(etag, proposed)
        saved = proposed if committed else _normalize(latest)
        return _remember(user_id, saved) if saved is not None else None
    except Exception as error:
        logger.warning(
            "Farm centre establish failed operation_id=%s duration_ms=%.0f error_type=%s",
            operation_id, (time.perf_counter() - started) * 1000, type(error).__name__,
        )
        return None


def get_farm_center(user_id: str, operation_id: str = "unknown") -> dict | None:
    if operation_id == "unknown":
        operation_id = uuid.uuid4().hex[:12]
    cached = _cached(user_id)
    if cached is not None:
        return cached
    started = time.perf_counter()
    try:
        center = _normalize(_bounded_reference(f"users/{user_id}/farm_center").get())
        return _remember(user_id, center) if center is not None else None
    except Exception as error:
        logger.warning(
            "Farm centre read failed operation_id=%s duration_ms=%.0f error_type=%s",
            operation_id, (time.perf_counter() - started) * 1000, type(error).__name__,
        )
        return None
