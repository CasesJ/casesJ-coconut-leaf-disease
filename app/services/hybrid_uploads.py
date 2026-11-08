"""Hybrid-only direct-upload coordination; no image decoding occurs here."""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import timedelta

from firebase_admin import firestore

from app.core import config
from app.infrastructure.cloudinary_storage import (
    CloudinaryStorageError,
    create_hybrid_upload_signature,
    verify_hybrid_uploaded_asset,
)
from app.services.hybrid_jobs import (
    HybridJobError,
    HybridJobStore,
    IdempotencyConflict,
    UploadIntentExpired,
    UploadQuotaExceeded,
)

logger = logging.getLogger(__name__)


def _public_id(user_id: str, idempotency_key: str) -> str:
    # Stable only within one authenticated user's key namespace. It makes a
    # retry reuse the exact authorization without exposing a guessable ID.
    digest = hashlib.sha256(f"{user_id}:{idempotency_key}".encode()).hexdigest()
    return f"neuralcoco/hybrid-original/{digest}.jpg"


def _store() -> HybridJobStore:
    # Firebase Admin uses the already-initialised production project in normal
    # operation. Tests replace this boundary with a fake or localhost emulator.
    return HybridJobStore(firestore.client())


def _record_timing_best_effort(store: HybridJobStore, job_id: str, stage: str,
                               values: dict[str, float]) -> None:
    """Persist diagnostics without changing a successful upload operation."""
    try:
        store.record_timing(job_id, values)
    except Exception:
        # Diagnostics must never turn an issued authorization or a verified
        # upload into a browser-visible failure.  Keep this log deliberately
        # limited to the opaque job ID and a fixed stage name.
        logger.warning("Hybrid timing persistence failed job_id=%s stage=%s", job_id, stage)


def create_upload_authorization(*, user_id: str, idempotency_key: str,
                                source_sha256: str, source_bytes: int,
                                route_type: str = "detection", user_email: str | None = None) -> dict:
    started = time.perf_counter()
    if route_type != "detection":
        raise ValueError("Unsupported hybrid detection route")
    if source_bytes > config.MAX_IMAGE_UPLOAD_BYTES:
        raise ValueError("Image content exceeds the configured limit")
    store = _store()
    expires_at = store.now() + timedelta(seconds=config.HYBRID_UPLOAD_TTL_SECONDS)
    public_id = _public_id(user_id, idempotency_key)
    job_id, created = store.create_upload_intent(
        user_id=user_id, idempotency_key=idempotency_key, public_id=public_id,
        source_sha256=source_sha256, source_bytes=source_bytes,
        route_type=route_type, upload_expires_at=expires_at,
        daily_limit=config.HYBRID_UPLOAD_DAILY_LIMIT, user_email=user_email,
    )
    intent = store.get_upload_intent(job_id, user_id)
    if intent.get("status") != "upload_intent":
        # A previously used idempotency key is never a new upload capability.
        raise IdempotencyConflict("Upload authorization has already been used")
    if intent.get("upload_expires_at") <= store.now():
        store.expire_upload_intent(job_id, user_id)
        raise UploadIntentExpired("Upload authorization has expired")
    authorization = create_hybrid_upload_signature(
        public_id=public_id, job_id=job_id, user_id=user_id,
        timestamp=int(time.time()),
    )
    duration_ms = (time.perf_counter() - started) * 1000
    _record_timing_best_effort(
        store, job_id, "render_authorization", {"render_authorization_ms": duration_ms},
    )
    logger.info("Hybrid timing job_id=%s stage=render_authorization duration_ms=%.1f", job_id, duration_ms)
    return {
        "job_id": job_id,
        "created": created,
        "expires_at": intent["upload_expires_at"],
        # Cloudinary's Upload API endpoint is /raw/upload.  The separately
        # signed `type=authenticated` form parameter controls private delivery.
        "upload_url": f"https://api.cloudinary.com/v1_1/{authorization['cloud_name']}/raw/upload",
        "upload_parameters": authorization,
    }


def confirm_upload(*, user_id: str, job_id: str, asset_id: str) -> dict:
    started = time.perf_counter()
    store = _store()
    intent = store.get_upload_intent(job_id, user_id)
    if intent.get("status") == "pending":
        # Idempotent confirmation still verifies the original ownership/asset
        # reference stored in Firestore, never a browser-supplied reference.
        existing = intent.get("cloudinary_asset") or {}
        if existing.get("asset_id") == asset_id:
            return intent
        raise IdempotencyConflict("Upload intent is already bound to another asset")
    if intent.get("upload_expires_at") <= store.now():
        store.expire_upload_intent(job_id, user_id)
        raise UploadIntentExpired("Upload authorization has expired")
    verification_started = time.perf_counter()
    asset = verify_hybrid_uploaded_asset(
        public_id=intent["public_id"], job_id=job_id, user_id=user_id,
        expected_bytes=intent["source_bytes"], asset_id=asset_id,
    )
    verification_ms = (time.perf_counter() - verification_started) * 1000
    confirmed = store.confirm_verified_upload(job_id, user_id, asset)
    duration_ms = (time.perf_counter() - started) * 1000
    _record_timing_best_effort(store, job_id, "render_confirmation", {
        "render_confirmation_verification_ms": verification_ms,
        "render_confirmation_total_ms": duration_ms,
    })
    logger.info(
        "Hybrid timing job_id=%s stage=render_confirmation duration_ms=%.1f verification_ms=%.1f",
        job_id, duration_ms, verification_ms,
    )
    return confirmed


def upload_status(*, user_id: str, job_id: str) -> dict:
    """Return a deliberately small owner-scoped browser status projection."""
    job = _store().get_upload_intent(job_id, user_id)
    result = job.get("result_reference") if isinstance(job.get("result_reference"), dict) else {}
    diagnostics = result.get("diagnostics") if isinstance(result.get("diagnostics"), dict) else {}
    stored_timing = job.get("timing") if isinstance(job.get("timing"), dict) else {}

    def stamp(name):
        value = job.get(name)
        return value.isoformat() if hasattr(value, "isoformat") else None

    response = {
        "job_id": job_id, "status": job.get("status"),
        "record_commit_status": job.get("record_commit_status"), "record_id": job.get("record_id"),
        "timing": {
            "upload_verified_at": stamp("upload_verified_at"), "claimed_at": stamp("claimed_at"),
            "completed_at": stamp("completed_at"), "record_committed_at": stamp("record_committed_at"),
            "worker_download_ms": diagnostics.get("worker_download_ms"),
            "worker_inference_total_ms": diagnostics.get("worker_inference_total_ms"),
            "measured_ms": {
                **{name: stored_timing[name] for name in stored_timing if name in {
                    "render_authorization_ms", "render_confirmation_verification_ms",
                    "render_confirmation_total_ms", "render_completion_job_ms",
                    "render_record_persistence_ms",
                }},
                **{name: diagnostics[name] for name in diagnostics if name in {
                    "worker_claim_request_ms", "worker_download_ms", "worker_validation_ms",
                    "worker_metadata_gps_ms", "worker_decode_ms", "worker_model_load_ms",
                    "worker_inference_ms", "worker_inference_total_ms",
                    "worker_thumbnail_generation_ms", "worker_thumbnail_initial_inspection_ms",
                    "worker_thumbnail_upload_ms", "worker_thumbnail_validation_ms",
                }},
            },
        },
    }
    if job.get("status") == "completed":
        # This is a response projection, not the raw Firestore job document:
        # no asset reference, signed URL, source hash, source byte count, XMP
        # blob, worker identity, lease detail, or private GPS is returned.
        response["result"] = {
            "detections": result.get("detections") if isinstance(result.get("detections"), list) else [],
            "image_dimensions": result.get("source_dimensions") if isinstance(result.get("source_dimensions"), dict) else {},
            "recorded_count": int(result.get("recordable_detection_count") or 0),
        }
    return response


__all__ = [
    "CloudinaryStorageError", "HybridJobError", "IdempotencyConflict",
    "UploadIntentExpired", "UploadQuotaExceeded", "confirm_upload",
    "create_upload_authorization", "upload_status",
]
