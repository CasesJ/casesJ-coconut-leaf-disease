"""Dormant, HMAC-authenticated control plane for outbound local workers."""
from __future__ import annotations

import hmac
import json
import logging
import re
import time
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.core.config import (
    HYBRID_WORKER_AUTH_SKEW_SECONDS, HYBRID_WORKER_LEASE_SECONDS,
    HYBRID_WORKER_NONCE_TTL_SECONDS, HYBRID_WORKER_SHARED_SECRET,
)
from app.core.resource_metrics import process_memory_metrics
from app.services.hybrid_jobs import HybridJobError, HybridJobStore, LeaseConflict
from app.services.hybrid_worker_auth import canonical_worker_message

router = APIRouter(prefix="/hybrid/worker", tags=["hybrid-worker-inactive"])
logger = logging.getLogger(__name__)
_WORKER_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_JOB_ID = re.compile(r"^[a-f0-9]{32}$")
_LEASE_TOKEN = re.compile(r"^[a-f0-9]{32}$")
_MAX_BODY = 256 * 1024


def _log_render_memory(stage: str) -> None:
    """Sample existing RSS/cgroup diagnostics during the bounded worker cycle."""
    metrics = process_memory_metrics()

    def mib(value):
        return round(value / (1024 * 1024), 1) if value is not None else "unavailable"

    logger.info(
        "Hybrid Render memory stage=%s rss_mib=%s cgroup_mib=%s cgroup_limit_mib=%s oom_kill=%s",
        stage, mib(metrics["rss_bytes"]), mib(metrics["cgroup_current_bytes"]),
        mib(metrics["cgroup_limit_bytes"]),
        metrics["cgroup_oom_kill"] if metrics["cgroup_oom_kill"] is not None else "unavailable",
    )


def _store() -> HybridJobStore:
    from firebase_admin import firestore  # noqa: PLC0415
    return HybridJobStore(firestore.client())


async def _authenticated_payload(request: Request) -> tuple[str, dict]:
    declared_size = request.headers.get("content-length")
    if declared_size is not None:
        try:
            if int(declared_size) > _MAX_BODY:
                raise HTTPException(413, "Worker request is too large")
        except ValueError:
            raise HTTPException(400, "Worker request size is invalid") from None
    buffered = bytearray()
    async for chunk in request.stream():
        if len(buffered) + len(chunk) > _MAX_BODY:
            raise HTTPException(413, "Worker request is too large")
        buffered.extend(chunk)
    body = bytes(buffered)
    worker_id = request.headers.get("X-Hybrid-Worker-Id", "")
    nonce = request.headers.get("X-Hybrid-Nonce", "")
    signature = request.headers.get("X-Hybrid-Signature", "")
    try:
        timestamp = int(request.headers.get("X-Hybrid-Timestamp", ""))
    except ValueError:
        raise HTTPException(401, "Worker authentication failed") from None
    if (not _WORKER_ID.fullmatch(worker_id) or not _WORKER_ID.fullmatch(nonce)
            or not re.fullmatch(r"[a-f0-9]{64}", signature)
            or abs(int(time.time()) - timestamp) > HYBRID_WORKER_AUTH_SKEW_SECONDS):
        raise HTTPException(401, "Worker authentication failed")
    expected = hmac.new(
        HYBRID_WORKER_SHARED_SECRET.encode(),
        canonical_worker_message(request.method, request.url.path, timestamp, nonce, body),
        "sha256",
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "Worker authentication failed")
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        raise HTTPException(400, "Worker request JSON is invalid") from None
    if not isinstance(payload, dict):
        raise HTTPException(400, "Worker request JSON is invalid")
    try:
        await run_in_threadpool(
            _store().consume_worker_nonce, nonce,
            _store().now() + timedelta(seconds=HYBRID_WORKER_NONCE_TTL_SECONDS),
        )
    except (LeaseConflict, HybridJobError, ValueError):
        raise HTTPException(401, "Worker authentication failed") from None
    return worker_id, payload


def _lease_token(payload: dict) -> str:
    token = payload.get("lease_token")
    if not isinstance(token, str) or not _LEASE_TOKEN.fullmatch(token):
        raise HTTPException(409, "Worker lease is no longer valid")
    return token


def _job_response(job: dict | None) -> dict:
    if not job:
        return {"job": None}
    # No signed URL, source bytes, API credential, image content, or private
    # GPS is sent by the coordinator. The worker derives a short-lived URL
    # locally from its private Cloudinary credential.
    return {"job": {
        "id": job["id"], "user_id": job["user_id"], "route_type": job["route_type"],
        "public_id": job["public_id"], "source_sha256": job["source_sha256"],
        "source_bytes": job["source_bytes"], "cloudinary_asset": job.get("cloudinary_asset"),
        "lease_token": job["lease_token"],
        "lease_expires_at": job.get("lease_expires_at").isoformat() if job.get("lease_expires_at") else None,
    }}


@router.post("/claim")
async def claim(request: Request):
    worker_id, _ = await _authenticated_payload(request)
    store = _store()
    try:
        await run_in_threadpool(store.recover_expired_leases)
        job = await run_in_threadpool(store.claim, worker_id, HYBRID_WORKER_LEASE_SECONDS)
        return _job_response(job)
    except HybridJobError:
        raise HTTPException(503, "Job coordinator is temporarily unavailable") from None


@router.post("/jobs/{job_id}/renew")
async def renew(job_id: str, request: Request):
    if not _JOB_ID.fullmatch(job_id):
        raise HTTPException(404, "Job not found")
    worker_id, payload = await _authenticated_payload(request)
    try:
        job = await run_in_threadpool(
            _store().renew_lease, job_id, worker_id, _lease_token(payload), HYBRID_WORKER_LEASE_SECONDS,
        )
        return {"lease_expires_at": job["lease_expires_at"].isoformat()}
    except LeaseConflict:
        raise HTTPException(409, "Worker lease is no longer valid") from None


@router.post("/jobs/{job_id}/complete")
async def complete(job_id: str, request: Request):
    if not _JOB_ID.fullmatch(job_id):
        raise HTTPException(404, "Job not found")
    worker_id, payload = await _authenticated_payload(request)
    result = payload.get("result")
    if not isinstance(result, dict) or len(json.dumps(result, separators=(",", ":"))) > _MAX_BODY:
        raise HTTPException(400, "Worker result is invalid")
    try:
        store = _store()
        _log_render_memory("before_result_commit")
        job = await run_in_threadpool(store.complete, job_id, worker_id, _lease_token(payload), result)
        # Record projection is idempotent and transactionally binds the job to
        # its canonical record. A crash after completion is recoverable by the
        # bounded reconciliation endpoint; it cannot create a partial record.
        from app.services.hybrid_record_commit import commit_completed_job  # noqa: PLC0415
        outcome = await run_in_threadpool(commit_completed_job, store.db, job_id)
        _log_render_memory("after_result_commit")
        return {"status": job["status"], **outcome}
    except LeaseConflict:
        raise HTTPException(409, "Worker lease is no longer valid") from None


@router.post("/reconcile")
async def reconcile(request: Request):
    """Bounded recovery plus read-only orphan review; never deletes assets."""
    await _authenticated_payload(request)
    _log_render_memory("worker_reconcile")
    from app.services.hybrid_record_commit import reconcile_completed_jobs  # noqa: PLC0415
    try:
        store = _store()
        outcomes = await run_in_threadpool(reconcile_completed_jobs, store.db, 10)
        cleanup_candidates = await run_in_threadpool(store.review_cleanup_candidates, 25)
        return {"reconciled": len(outcomes), "cleanup_candidates": len(cleanup_candidates)}
    except HybridJobError:
        raise HTTPException(503, "Job coordinator is temporarily unavailable") from None


@router.post("/jobs/{job_id}/fail")
async def fail(job_id: str, request: Request):
    if not _JOB_ID.fullmatch(job_id):
        raise HTTPException(404, "Job not found")
    worker_id, payload = await _authenticated_payload(request)
    retryable = bool(payload.get("retryable"))
    # Failure diagnostics are deliberately categorical and never contain
    # provider URLs, file paths, metadata or exception text.
    category = str(payload.get("category") or "unknown")
    if category not in {"source_integrity", "cloudinary", "inference", "network", "unknown"}:
        raise HTTPException(400, "Worker failure category is invalid")
    try:
        job = await run_in_threadpool(_store().fail, job_id, worker_id, _lease_token(payload), retryable)
        return {"status": job["status"], "retry_count": job["retry_count"]}
    except LeaseConflict:
        raise HTTPException(409, "Worker lease is no longer valid") from None
