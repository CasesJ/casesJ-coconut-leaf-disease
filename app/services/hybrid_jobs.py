"""Isolated Firestore state machine for future hybrid-inference jobs.

No API router imports this module in Stage 2A.  It deliberately has no
Cloudinary, image, model, or browser dependencies.
"""
from __future__ import annotations

import hashlib
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import uuid4
from zoneinfo import ZoneInfo


class HybridJobError(RuntimeError):
    pass


class IdempotencyConflict(HybridJobError):
    pass


class InvalidTransition(HybridJobError):
    pass


class LeaseConflict(HybridJobError):
    pass


class UploadIntentExpired(HybridJobError):
    pass


class UploadQuotaExceeded(HybridJobError):
    pass


class RetryableTransactionError(HybridJobError):
    """Adapter-level signal used by Firestore integration to retry a conflict."""


JOB_COLLECTION = "hybrid_inference_jobs"
IDEMPOTENCY_COLLECTION = "hybrid_inference_idempotency"
QUOTA_COLLECTION = "hybrid_inference_upload_quotas"
WORKER_NONCE_COLLECTION = "hybrid_inference_worker_nonces"
UPLOAD_INTENT = "upload_intent"
PENDING = "pending"
LEASED = "leased"
RETRYABLE_FAILED = "retryable_failed"
FAILED = "failed"
COMPLETED = "completed"
CLEANUP_PENDING = "cleanup_pending"
CLEANED = "cleaned"

_ALLOWED = {
    UPLOAD_INTENT: {PENDING, CLEANUP_PENDING},
    PENDING: {LEASED, CLEANUP_PENDING},
    LEASED: {PENDING, RETRYABLE_FAILED, FAILED, COMPLETED, CLEANUP_PENDING},
    RETRYABLE_FAILED: {PENDING, LEASED, CLEANUP_PENDING},
    FAILED: {CLEANUP_PENDING},
    CLEANUP_PENDING: {CLEANED},
    COMPLETED: set(),
    CLEANED: set(),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _snapshot_dict(snapshot):
    # ``Transaction.get(document_ref)`` in google-cloud-firestore returns a
    # one-item iterator, whereas the small in-memory fake used by unit tests
    # returns a snapshot directly.  Accept both without making the domain
    # state machine depend on either implementation.
    if snapshot is not None and not hasattr(snapshot, "to_dict") and not isinstance(snapshot, dict):
        try:
            snapshot = next(iter(snapshot), None)
        except TypeError:
            pass
    if snapshot is None:
        return None
    if hasattr(snapshot, "exists") and not snapshot.exists:
        return None
    return snapshot.to_dict() if hasattr(snapshot, "to_dict") else snapshot


class HybridJobStore:
    """Transaction-only job store; ``db`` supplies Firestore-compatible refs."""

    def __init__(self, db, now: Callable[[], datetime] = _now, *, max_retries=3, transaction_attempts=3):
        self.db, self.now = db, now
        self.max_retries, self.transaction_attempts = max_retries, transaction_attempts

    def _run(self, callback):
        # The native client must begin a transaction before its first read and
        # provides the only correct way to retry an optimistic commit. Each
        # retry deliberately starts a *new* transaction, so a worker that lost
        # a queue-claim conflict re-reads the winner's lease rather than
        # repeatedly using the original snapshot. Keep the simple adapter path
        # below for the deliberately SDK-free fake.
        transaction = self.db.transaction()
        if transaction.__class__.__module__.startswith("google.cloud.firestore"):
            from google.cloud.firestore_v1.transaction import transactional

            last_error = None
            for attempt in range(self.transaction_attempts):
                transaction = self.db.transaction()
                transaction._max_attempts = 1
                try:
                    return transactional(callback)(transaction)
                except ValueError as error:
                    # The SDK wraps an aborted one-attempt transaction in
                    # ValueError. Start fresh rather than retaining retry_id.
                    if error.__cause__ is None or error.__cause__.__class__.__name__ != "Aborted":
                        raise
                    last_error = error
                    # The local emulator retains a pessimistic lock for about
                    # two seconds after an aborted concurrent commit. A
                    # bounded, jittered backoff lets the next fresh read
                    # observe the winner instead of creating a lock convoy.
                    if attempt + 1 < self.transaction_attempts:
                        time.sleep(2.25 * (attempt + 1) + random.uniform(0, 0.25))
            raise RetryableTransactionError("Firestore transaction retry limit reached") from last_error

        last_error = None
        for _ in range(self.transaction_attempts):
            transaction = self.db.transaction()
            try:
                result = callback(transaction)
                transaction.commit()
                return result
            except RetryableTransactionError as error:
                last_error = error
            except Exception as error:
                # Firestore uses an optimistic commit and reports a changed
                # read version as google.api_core.exceptions.Aborted.  Avoid
                # importing the SDK into this isolated module solely for one
                # exception class; fakes can expose the same semantic error.
                if error.__class__.__name__ != "Aborted":
                    raise
                last_error = RetryableTransactionError("Firestore transaction conflict")
        raise last_error or RetryableTransactionError("Transaction retry limit reached")

    def _job_ref(self, job_id):
        return self._collection().document(job_id)

    def _collection(self):
        return self.db.collection(JOB_COLLECTION)

    def _key_ref(self, user_id, key):
        digest = hashlib.sha256(f"{user_id}:{key}".encode()).hexdigest()
        return self.db.collection(IDEMPOTENCY_COLLECTION).document(digest)

    def _quota_ref(self, user_id, now):
        day = now.astimezone(ZoneInfo("Asia/Manila")).strftime("%Y%m%d")
        digest = hashlib.sha256(f"{user_id}:{day}".encode()).hexdigest()
        return self.db.collection(QUOTA_COLLECTION).document(digest)

    def _nonce_ref(self, nonce: str):
        return self.db.collection(WORKER_NONCE_COLLECTION).document(
            hashlib.sha256(nonce.encode()).hexdigest()
        )

    def consume_worker_nonce(self, nonce: str, expires_at: datetime) -> None:
        """Atomically consume a signed worker-request nonce exactly once."""
        if not isinstance(nonce, str) or not nonce or len(nonce) > 128:
            raise ValueError("Worker request nonce is invalid")
        if not isinstance(expires_at, datetime) or expires_at <= self.now():
            raise ValueError("Worker nonce expiration is invalid")
        ref = self._nonce_ref(nonce)

        def operation(txn):
            if _snapshot_dict(txn.get(ref)):
                raise LeaseConflict("Worker request replay detected")
            txn.create(ref, {"expires_at": expires_at, "created_at": self.now()})
        self._run(operation)

    def _jobs_with_statuses(self, txn, statuses):
        """Read candidate jobs through a native Firestore query or fake collection."""
        collection = self._collection()
        if hasattr(collection, "where"):
            from google.cloud.firestore_v1.base_query import FieldFilter

            # Equality queries avoid relying on a composite / ``in`` query
            # plan while inside an optimistic emulator transaction.  The
            # caller still checks the state before every transition.
            snapshots = []
            for status in statuses:
                snapshots.extend(txn.get(collection.where(filter=FieldFilter("status", "==", status))))
            return snapshots
        return txn.get(collection)

    def create_upload_intent(self, *, user_id, idempotency_key, public_id,
                             source_sha256, source_bytes, route_type,
                             upload_expires_at=None, daily_limit=None, user_email=None):
        if not all(isinstance(v, str) and v for v in (user_id, idempotency_key, public_id, source_sha256, route_type)):
            raise ValueError("Hybrid job identifiers are required")
        if len(source_sha256) != 64 or any(c not in "0123456789abcdef" for c in source_sha256.lower()):
            raise ValueError("Expected source SHA-256 is invalid")
        if not isinstance(source_bytes, int) or source_bytes < 1:
            raise ValueError("Expected source size is invalid")
        if upload_expires_at is not None and not isinstance(upload_expires_at, datetime):
            raise ValueError("Upload expiration must be a datetime")
        if daily_limit is not None and (not isinstance(daily_limit, int) or daily_limit < 1):
            raise ValueError("Daily upload limit is invalid")
        if user_email is not None and (not isinstance(user_email, str) or len(user_email) > 320):
            raise ValueError("Upload user email is invalid")
        fingerprint = {"public_id": public_id, "source_sha256": source_sha256,
                       "source_bytes": source_bytes, "route_type": route_type}
        key_ref = self._key_ref(user_id, idempotency_key)

        def operation(txn):
            existing = _snapshot_dict(txn.get(key_ref))
            if existing:
                if existing.get("fingerprint") != fingerprint:
                    raise IdempotencyConflict("Idempotency key belongs to a different upload")
                return existing["job_id"], False
            now, job_id = self.now(), uuid4().hex
            if upload_expires_at is not None and upload_expires_at <= now:
                raise UploadIntentExpired("Upload intent has already expired")
            if daily_limit is not None:
                quota_ref = self._quota_ref(user_id, now)
                quota = _snapshot_dict(txn.get(quota_ref)) or {}
                count = int(quota.get("count") or 0)
                if count >= daily_limit:
                    raise UploadQuotaExceeded("Daily hybrid upload limit reached")
                quota_update = {
                    "user_id": user_id, "count": count + 1,
                    "updated_at": now,
                }
                if quota:
                    txn.update(quota_ref, quota_update)
                else:
                    txn.create(quota_ref, {**quota_update, "created_at": now})
            job = {"id": job_id, "user_id": user_id, "idempotency_key": idempotency_key,
                   "user_email": user_email or "",
                   **fingerprint, "status": UPLOAD_INTENT, "created_at": now, "updated_at": now,
                   "lease_owner": None, "lease_token": None,
                   "lease_expires_at": None, "retry_count": 0,
                   "result_reference": None, "cleanup_status": "not_required",
                   "upload_expires_at": upload_expires_at,
                   "upload_status": "authorized" if upload_expires_at else "not_authorized",
                   "cloudinary_asset": None,
                   "source_hash_status": "expected_only"}
            txn.create(self._job_ref(job_id), job)
            txn.create(key_ref, {"job_id": job_id, "user_id": user_id, "fingerprint": fingerprint, "created_at": now})
            return job_id, True
        return self._run(operation)

    def get_upload_intent(self, job_id, user_id):
        """Return an upload intent only to its owner, without a collection scan."""
        def operation(txn):
            job = _snapshot_dict(txn.get(self._job_ref(job_id)))
            if not job or job.get("user_id") != user_id:
                raise HybridJobError("Hybrid upload intent not found")
            return job
        return self._run(operation)

    def get_job(self, job_id):
        """Coordinator-only job read; callers must enforce their own authority."""
        return self._run(lambda txn: _snapshot_dict(txn.get(self._job_ref(job_id))))

    def confirm_verified_upload(self, job_id, user_id, asset_reference):
        """Atomically bind an Admin-verified Cloudinary asset and queue the job."""
        if not isinstance(asset_reference, dict):
            raise ValueError("Verified Cloudinary asset is required")

        def operation(txn):
            ref = self._job_ref(job_id)
            job = _snapshot_dict(txn.get(ref))
            if not job or job.get("user_id") != user_id:
                raise HybridJobError("Hybrid upload intent not found")
            if job.get("public_id") != asset_reference.get("public_id"):
                raise IdempotencyConflict("Cloudinary public ID does not match the upload intent")
            if job.get("status") == PENDING:
                if job.get("cloudinary_asset") == asset_reference:
                    return job
                raise IdempotencyConflict("Upload intent is already bound to another asset")
            if job.get("status") != UPLOAD_INTENT:
                raise InvalidTransition("Upload intent is no longer confirmable")
            expires_at = job.get("upload_expires_at")
            if expires_at is not None and expires_at <= self.now():
                change = {"status": CLEANUP_PENDING, "updated_at": self.now(),
                          "cleanup_status": "pending", "upload_status": "expired"}
                txn.update(ref, change)
                # Return a sentinel so this write commits before the caller
                # receives the expiry error. Raising inside a Firestore
                # callback would roll the cleanup marker back.
                return None
            change = {
                "status": PENDING, "updated_at": self.now(),
                "upload_status": "verified", "cloudinary_asset": dict(asset_reference),
                # Cloudinary's Admin API verifies identity, type, ownership
                # context, and byte count; it does not expose SHA-256. The
                # local worker will stream-verify the expected SHA-256 before
                # inference in a later stage.
                "source_hash_status": "pending_worker_verification",
            }
            txn.update(ref, change)
            return {**job, **change}
        confirmed = self._run(operation)
        if confirmed is None:
            raise UploadIntentExpired("Upload intent has expired")
        return confirmed

    def expire_upload_intent(self, job_id, user_id):
        """Mark an unconfirmed expired intent for later, feature-flagged cleanup."""
        def operation(txn):
            ref = self._job_ref(job_id)
            job = _snapshot_dict(txn.get(ref))
            if not job or job.get("user_id") != user_id:
                raise HybridJobError("Hybrid upload intent not found")
            if job.get("status") == CLEANUP_PENDING:
                return job
            if job.get("status") != UPLOAD_INTENT:
                raise InvalidTransition("Only unconfirmed uploads can expire")
            if job.get("upload_expires_at") is None or job["upload_expires_at"] > self.now():
                raise UploadIntentExpired("Upload intent has not expired")
            change = {"status": CLEANUP_PENDING, "updated_at": self.now(),
                      "cleanup_status": "pending", "upload_status": "expired"}
            txn.update(ref, change)
            return {**job, **change}
        return self._run(operation)

    def _transition(self, txn, job_id, target, *, updates=None, owner=None, lease_token=None):
        ref = self._job_ref(job_id); job = _snapshot_dict(txn.get(ref))
        if not job:
            raise HybridJobError("Hybrid job not found")
        if target not in _ALLOWED.get(job["status"], set()):
            raise InvalidTransition(f"{job['status']} cannot transition to {target}")
        if owner is not None:
            if (job.get("lease_owner") != owner or not lease_token
                    or job.get("lease_token") != lease_token
                    or job.get("lease_expires_at") is None):
                raise LeaseConflict("Worker does not own this lease")
            if job["lease_expires_at"] <= self.now():
                raise LeaseConflict("Worker lease has expired")
        change = {"status": target, "updated_at": self.now(), **(updates or {})}
        txn.update(ref, change)
        return {**job, **change}

    def mark_verified(self, job_id):
        return self._run(lambda txn: self._transition(
            txn, job_id, PENDING, updates={"upload_verified_at": self.now()},
        ))

    def claim(self, worker_id, lease_seconds):
        if not worker_id or lease_seconds < 1:
            raise ValueError("Worker and lease duration are required")

        # Native Firestore emulators (and production) hold a broad lock for a
        # query made inside a read/write transaction. Select one bounded
        # candidate first, then atomically re-read and claim that *document*.
        # The second read is authoritative; a stale candidate safely returns
        # None after another worker has acquired it.
        collection = self._collection()
        if hasattr(collection, "where"):
            from google.cloud.firestore_v1.base_query import FieldFilter

            candidate = None
            for status in (PENDING, RETRYABLE_FAILED):
                candidate = next(iter(collection.where(
                    filter=FieldFilter("status", "==", status)
                ).limit(1).stream()), None)
                if candidate is not None:
                    break
            if candidate is None:
                return None

            def claim_candidate(txn):
                ref = self._job_ref(candidate.id)
                job = _snapshot_dict(txn.get(ref))
                if not job or job["status"] not in {PENDING, RETRYABLE_FAILED}:
                    return None
                now = self.now()
                change = {"status": LEASED, "updated_at": now,
                          "lease_owner": worker_id,
                          "lease_token": uuid4().hex,
                          "lease_expires_at": now + timedelta(seconds=lease_seconds),
                          "claimed_at": now}
                txn.update(ref, change)
                return {**job, **change}

            return self._run(claim_candidate)

        def operation(txn):
            now = self.now()
            for snapshot in self._jobs_with_statuses(txn, {PENDING, RETRYABLE_FAILED}):
                job = _snapshot_dict(snapshot)
                if job and job["status"] in {PENDING, RETRYABLE_FAILED}:
                    return self._transition(txn, job["id"], LEASED, updates={
                        "lease_owner": worker_id, "lease_expires_at": now + timedelta(seconds=lease_seconds),
                        "lease_token": uuid4().hex,
                        "claimed_at": now,
                    }, owner=None)
            return None
        return self._run(operation)

    def renew_lease(self, job_id, worker_id, lease_token, lease_seconds):
        def operation(txn):
            job = _snapshot_dict(txn.get(self._job_ref(job_id)))
            if (not job or job["status"] != LEASED or job.get("lease_owner") != worker_id
                    or not lease_token or job.get("lease_token") != lease_token
                    or job.get("lease_expires_at") is None
                    or job["lease_expires_at"] <= self.now()):
                raise LeaseConflict("Lease is invalid or expired")
            change = {"lease_expires_at": self.now() + timedelta(seconds=lease_seconds), "updated_at": self.now()}
            txn.update(self._job_ref(job_id), change); return {**job, **change}
        return self._run(operation)

    def complete(self, job_id, worker_id, lease_token, result_reference):
        def operation(txn):
            job = _snapshot_dict(txn.get(self._job_ref(job_id)))
            # A coordinator response can be lost after the first commit. Permit
            # only the same worker to observe its already committed result;
            # never replace it or accept an old worker's different payload.
            if job and job.get("status") == COMPLETED:
                if (lease_token and job.get("completed_by") == worker_id
                        and job.get("completed_lease_token") == lease_token
                        and job.get("result_reference") == result_reference):
                    return job
                raise LeaseConflict("Worker cannot replace a completed result")
            return self._transition(
                txn, job_id, COMPLETED,
                updates={"result_reference": result_reference, "completed_by": worker_id,
                         "completed_lease_token": lease_token,
                         "lease_owner": None, "lease_token": None, "lease_expires_at": None,
                         "record_commit_status": "pending", "completed_at": self.now()}, owner=worker_id,
                lease_token=lease_token,
            )
        return self._run(operation)

    def fail(self, job_id, worker_id, lease_token, retryable=True):
        def operation(txn):
            current = _snapshot_dict(txn.get(self._job_ref(job_id))) or {}
            count = current.get("retry_count", 0) + 1
            target = RETRYABLE_FAILED if retryable and count <= self.max_retries else FAILED
            return self._transition(txn, job_id, target, updates={
                "retry_count": count, "lease_owner": None, "lease_token": None,
                "lease_expires_at": None,
            }, owner=worker_id, lease_token=lease_token)
        return self._run(operation)

    def recover_expired_leases(self):
        def operation(txn):
            recovered = []
            collection = self._collection()
            if hasattr(collection, "where"):
                from google.cloud.firestore_v1.base_query import FieldFilter
                # A single-field lease query is bounded and needs no composite
                # index.  Only a few expired jobs are recovered per poll.
                snapshots = txn.get(collection.where(
                    filter=FieldFilter("lease_expires_at", "<=", self.now())
                ).limit(25))
            else:
                snapshots = self._jobs_with_statuses(txn, {LEASED})
            changes = []
            for snapshot in snapshots:
                job = _snapshot_dict(snapshot)
                if job and job["status"] == LEASED and job.get("lease_expires_at") <= self.now():
                    count = int(job.get("retry_count") or 0) + 1
                    target = PENDING if count <= self.max_retries else FAILED
                    changes.append((self._job_ref(job["id"]), {
                        "status": target, "updated_at": self.now(),
                        "lease_owner": None, "lease_token": None, "lease_expires_at": None,
                        "retry_count": count,
                    }))
                    recovered.append(job["id"])
            for ref, change in changes:
                txn.update(ref, change)
            return recovered
        return self._run(operation)

    def review_cleanup_candidates(self, limit: int = 25) -> list[dict]:
        """Return a bounded, read-only review of assets awaiting cleanup.

        Automatic deletion intentionally remains outside this state machine.
        An operator can inspect these immutable identifiers before approving an
        exact Cloudinary deletion, and valid/completed records are never part
        of this query.
        """
        safe_limit = max(1, min(int(limit), 100))
        collection = self._collection()
        if not hasattr(collection, "where"):
            return []
        from google.cloud.firestore_v1.base_query import FieldFilter
        candidates = []
        for status in (CLEANUP_PENDING, FAILED, UPLOAD_INTENT):
            snapshots = collection.where(
                filter=FieldFilter("status", "==", status)
            ).limit(safe_limit).stream()
            for snapshot in snapshots:
                job = snapshot.to_dict() or {}
                if status == UPLOAD_INTENT and (
                        not job.get("upload_expires_at")
                        or job["upload_expires_at"] > self.now()):
                    continue
                asset = job.get("cloudinary_asset") if isinstance(job.get("cloudinary_asset"), dict) else {}
                job_id = str(job.get("id") or snapshot.id)
                candidates.append({
                    "job_id": job_id,
                    "user_id": str(job.get("user_id") or ""),
                    "status": status,
                    "cleanup_status": str(job.get("cleanup_status") or "not_reviewed"),
                    "original_public_id": str(asset.get("public_id") or job.get("public_id") or ""),
                    "original_asset_id": str(asset.get("asset_id") or ""),
                    "possible_thumbnail_public_id": f"neuralcoco/detection-thumbnail/{job_id}",
                    "requires_record_reference_check": True,
                })
        return candidates

    def mark_cleanup_pending(self, job_id):
        return self._run(lambda txn: self._transition(txn, job_id, CLEANUP_PENDING, updates={"cleanup_status": "pending"}))
