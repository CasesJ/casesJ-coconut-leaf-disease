"""Real Firestore Admin/Google SDK integration tests; never target production."""
from __future__ import annotations

import os
import socket
import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import firebase_admin
from firebase_admin import credentials, firestore as admin_firestore
from google.auth.credentials import AnonymousCredentials

from app.services.hybrid_jobs import (
    HybridJobStore, HybridJobError, IdempotencyConflict, LeaseConflict, COMPLETED,
    JOB_COLLECTION, IDEMPOTENCY_COLLECTION, QUOTA_COLLECTION, WORKER_NONCE_COLLECTION,
)
from app.services import hybrid_uploads
from app.services.hybrid_record_commit import commit_completed_job, reconcile_completed_jobs

PROJECT = "demo-coconutai-hybrid-tests"
HOST = "127.0.0.1:8080"


class EmulatorCredential(credentials.Base):
    """Never discovers ADC or service-account credentials in an emulator test."""

    def get_credential(self):
        return AnonymousCredentials()


def _guarded_client(monkeypatch):
    """Fail closed before a client exists unless this is the localhost demo."""
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", HOST)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", PROJECT)
    if os.environ.get("FIRESTORE_EMULATOR_HOST") != HOST or os.environ.get("GOOGLE_CLOUD_PROJECT") != PROJECT:
        raise RuntimeError("Hybrid integration tests require the localhost demo emulator")
    if PROJECT == "coconut-leaf-disease-dcf9a" or not PROJECT.startswith("demo-"):
        raise RuntimeError("Production Firebase project is forbidden in integration tests")
    try:
        with socket.create_connection(("127.0.0.1", 8080), timeout=1):
            pass
    except OSError as error:
        raise RuntimeError("Firestore Emulator is not reachable at 127.0.0.1:8080") from error
    # The production subsystem will receive its client from Firebase Admin.
    # A uniquely named app keeps this test fully isolated from any app that a
    # separate test suite may initialise in the same interpreter.
    app = firebase_admin.initialize_app(EmulatorCredential(), options={"projectId": PROJECT}, name=f"hybrid-emulator-{uuid4().hex}")
    return app, admin_firestore.client(app=app)


@pytest.fixture
def emulator_store(monkeypatch):
    app, client = _guarded_client(monkeypatch)
    # The emulator must respond before tests issue writes. A production endpoint
    # cannot satisfy the prior host/project guard.
    client.collection("_emulator_probe").document(uuid4().hex).get()
    prefix = uuid4().hex
    class PrefixedStore(HybridJobStore):
        def _job_ref(self, job_id):
            return self.db.collection(f"{JOB_COLLECTION}_{prefix}").document(job_id)
        def _key_ref(self, user_id, key):
            return self.db.collection(f"{IDEMPOTENCY_COLLECTION}_{prefix}").document(super()._key_ref(user_id, key).id)
        def _quota_ref(self, user_id, now):
            return self.db.collection(f"{QUOTA_COLLECTION}_{prefix}").document(super()._quota_ref(user_id, now).id)
        def _nonce_ref(self, nonce):
            return self.db.collection(f"{WORKER_NONCE_COLLECTION}_{prefix}").document(super()._nonce_ref(nonce).id)
        def _collection(self):
            return self.db.collection(f"{JOB_COLLECTION}_{prefix}")
    try:
        yield PrefixedStore(client)
    finally:
        firebase_admin.delete_app(app)


def _args(**overrides):
    value={"user_id":"farmer-a", "idempotency_key":"i"*16,
           "public_id":"neuralcoco/hybrid-original/"+"a"*32,
           "source_sha256":"b"*64, "source_bytes":123, "route_type":"detection"}
    value.update(overrides); return value


@pytest.mark.integration
def test_real_sdk_idempotency_and_conflict(emulator_store):
    job, created = emulator_store.create_upload_intent(**_args())
    assert created and emulator_store.create_upload_intent(**_args()) == (job, False)
    with pytest.raises(IdempotencyConflict):
        emulator_store.create_upload_intent(**_args(source_bytes=124))


@pytest.mark.integration
def test_orphan_review_is_bounded_and_read_only(emulator_store):
    job,_=emulator_store.create_upload_intent(**_args(user_id="orphan-review"))
    emulator_store.mark_cleanup_pending(job)
    before=emulator_store.get_job(job)
    candidates=emulator_store.review_cleanup_candidates(10)
    match=[item for item in candidates if item["job_id"] == job]
    assert len(match) == 1
    assert match[0]["original_public_id"] == before["public_id"]
    assert match[0]["requires_record_reference_check"] is True
    assert emulator_store.get_job(job) == before


@pytest.mark.integration
def test_real_sdk_concurrent_claim_and_stale_worker(emulator_store):
    job, _ = emulator_store.create_upload_intent(**_args())
    emulator_store.mark_verified(job)
    results=[]; errors=[]; start=threading.Barrier(2)
    def claim(worker):
        try:
            start.wait(timeout=5); results.append(emulator_store.claim(worker, 1))
        except Exception as error: errors.append(error)
    threads=[threading.Thread(target=claim,args=(worker,)) for worker in ("one","two")]
    [thread.start() for thread in threads]; [thread.join(15) for thread in threads]
    assert not errors and len([item for item in results if item]) == 1
    first=next(item for item in results if item)
    owner=first["lease_owner"]
    # Force expiry in the emulator document; a recovered lease must reject the old owner.
    emulator_store._job_ref(job).update({"lease_expires_at": datetime.now(timezone.utc)-timedelta(seconds=1)})
    assert emulator_store.recover_expired_leases() == [job]
    new_owner="two" if owner == "one" else "one"
    second=emulator_store.claim(new_owner, 60)
    assert second["lease_owner"] == new_owner
    with pytest.raises(LeaseConflict): emulator_store.complete(job, owner, first["lease_token"], {"stale": True})
    assert emulator_store.complete(job, new_owner, second["lease_token"], {"ok": True})["status"] == COMPLETED


@pytest.mark.integration
def test_real_sdk_same_worker_id_cannot_reuse_expired_claim(emulator_store):
    job, _ = emulator_store.create_upload_intent(**_args(idempotency_key="same-worker-lease"))
    emulator_store.mark_verified(job)
    first = emulator_store.claim("laptop", 60)
    emulator_store._job_ref(job).update({"lease_expires_at": datetime.now(timezone.utc)-timedelta(seconds=1)})
    assert emulator_store.recover_expired_leases() == [job]
    second = emulator_store.claim("laptop", 60)
    assert first["lease_token"] != second["lease_token"]
    with pytest.raises(LeaseConflict):
        emulator_store.renew_lease(job, "laptop", first["lease_token"], 60)
    with pytest.raises(LeaseConflict):
        emulator_store.fail(job, "laptop", first["lease_token"], True)
    with pytest.raises(LeaseConflict):
        emulator_store.complete(job, "laptop", first["lease_token"], {"stale": True})
    assert emulator_store.complete(job, "laptop", second["lease_token"], {"ok": True})["status"] == COMPLETED


@pytest.mark.integration
def test_real_sdk_conflict_retries_with_a_fresh_read(emulator_store):
    """Force an optimistic conflict and prove the losing callback runs again."""
    job, _ = emulator_store.create_upload_intent(**_args(idempotency_key="r" * 16))
    emulator_store.mark_verified(job)
    start = threading.Barrier(2)
    lock = threading.Lock()
    attempts, results, errors = {}, [], []

    def update(worker):
        local = threading.local()

        def operation(txn):
            current = txn.get(emulator_store._job_ref(job))
            snapshot = next(iter(current))
            assert snapshot.to_dict()["status"] == "pending"
            with lock:
                attempts[worker] = attempts.get(worker, 0) + 1
            if not getattr(local, "synchronised", False):
                local.synchronised = True
                start.wait(timeout=5)
            txn.update(emulator_store._job_ref(job), {"retry_probe": worker})
            return worker

        try:
            results.append(emulator_store._run(operation))
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=update, args=(worker,)) for worker in ("one", "two")]
    [thread.start() for thread in threads]
    [thread.join(15) for thread in threads]
    assert not errors and sorted(results) == ["one", "two"]
    # The emulator may abort either or both initial commits under its local
    # lock manager, but at least one callback must have been freshly retried.
    assert min(attempts.values()) >= 1 and max(attempts.values()) >= 2


@pytest.mark.integration
def test_real_sdk_hybrid_upload_intent_and_mocked_admin_confirmation(emulator_store, monkeypatch):
    """Firestore is real; no Cloudinary network or credentials are used."""
    monkeypatch.setattr(hybrid_uploads, "_store", lambda: emulator_store)
    monkeypatch.setattr(hybrid_uploads.config, "HYBRID_UPLOAD_TTL_SECONDS", 300)
    monkeypatch.setattr(hybrid_uploads.config, "HYBRID_UPLOAD_DAILY_LIMIT", 5)
    monkeypatch.setattr(hybrid_uploads, "create_hybrid_upload_signature", lambda **kw: {
        "cloud_name": "test", "api_key": "public", "signature": "mock", **kw,
    })
    created = hybrid_uploads.create_upload_authorization(
        user_id="farmer-a", idempotency_key="z" * 16,
        source_sha256="c" * 64, source_bytes=123,
    )
    assert created["created"] and created["upload_parameters"]["signature"] == "mock"
    monkeypatch.setattr(hybrid_uploads, "verify_hybrid_uploaded_asset", lambda **kw: {
        "provider": "cloudinary", "asset_id": "asset", "public_id": kw["public_id"],
        "byte_size": 123, "delivery_type": "authenticated",
    })
    queued = hybrid_uploads.confirm_upload(
        user_id="farmer-a", job_id=created["job_id"], asset_id="asset",
    )
    assert queued["status"] == "pending"
    assert hybrid_uploads.confirm_upload(
        user_id="farmer-a", job_id=created["job_id"], asset_id="asset",
    )["status"] == "pending"
    with pytest.raises(HybridJobError):
        hybrid_uploads.confirm_upload(user_id="farmer-b", job_id=created["job_id"], asset_id="asset")


@pytest.mark.integration
def test_real_sdk_worker_nonce_replay_is_rejected(emulator_store):
    expires = datetime.now(timezone.utc) + timedelta(seconds=60)
    emulator_store.consume_worker_nonce("worker-request-unique", expires)
    with pytest.raises(LeaseConflict):
        emulator_store.consume_worker_nonce("worker-request-unique", expires)


@pytest.mark.integration
def test_real_sdk_expired_worker_cannot_complete_without_reclaim(emulator_store):
    job, _ = emulator_store.create_upload_intent(**_args(idempotency_key="e" * 16))
    emulator_store.mark_verified(job)
    claim=emulator_store.claim("worker", 60)
    emulator_store._job_ref(job).update({"lease_expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)})
    with pytest.raises(LeaseConflict):
        emulator_store.complete(job, "worker", claim["lease_token"], {"stale": True})


@pytest.mark.integration
def test_real_sdk_concurrent_hybrid_record_projection_is_idempotent(emulator_store, monkeypatch):
    monkeypatch.setattr("app.services.hybrid_record_commit._farm_assignment", lambda *args: (None, False))
    user = f"farmer-{uuid4().hex}"
    job, _ = emulator_store.create_upload_intent(
        user_id=user, user_email="farmer@example.test", idempotency_key="q" * 16,
        public_id="neuralcoco/hybrid-original/" + "a" * 32 + ".jpg",
        source_sha256="c" * 64, source_bytes=99, route_type="detection",
    )
    emulator_store.mark_verified(job)
    claim=emulator_store.claim("worker", 60)
    emulator_store._job_ref(job).update({"cloudinary_asset": {
        "provider":"cloudinary", "asset_id":"asset", "public_id":"neuralcoco/hybrid-original/"+"a"*32+".jpg",
        "resource_type":"raw", "delivery_type":"authenticated", "version":1,
        "format":"jpg", "content_type":"image/jpeg", "byte_size":99,
    }})
    result={"schema":"hybrid-inference-result-v1", "detections":[{"class":"Bud Rot","confidence":0.9,"bbox":[1,2,30,40]}],
            "source_dimensions":{"width":100,"height":80}, "gps":{"source":"unknown"},
            "image_metadata":{"xmp":{"DJI":"retained"}}, "source_identity":{"sha256":"c"*64,"bytes":99},
            "thumbnail_asset":{"provider":"cloudinary", "asset_id":"thumb", "public_id":f"neuralcoco/detection-thumbnail/{job}",
                               "resource_type":"image", "delivery_type":"authenticated", "version":1,
                               "format":"jpg", "content_type":"image/jpeg", "byte_size":12}}
    emulator_store.complete(job, "worker", claim["lease_token"], result)
    results=[]; errors=[]; barrier=threading.Barrier(2)
    def commit():
        try:
            barrier.wait(timeout=5); results.append(commit_completed_job(emulator_store.db, job, store=emulator_store))
        except Exception as error: errors.append(error)
    threads=[threading.Thread(target=commit) for _ in range(2)]
    [thread.start() for thread in threads]; [thread.join(20) for thread in threads]
    assert not errors and len(results) == 2
    assert {item["record_id"] for item in results} == {f"hybrid_{job}"}
    record = emulator_store.db.collection("users").document(user).collection("detections").document(f"hybrid_{job}").get()
    assert record.exists and record.to_dict()["image_assets"]["original"]["resource_type"] == "raw"
    # Simulate a process interruption after the document commit but before the
    # job marker response was observed; reconciliation must converge safely.
    emulator_store._job_ref(job).update({"record_commit_status": "pending"})
    assert reconcile_completed_jobs(emulator_store.db, store=emulator_store) == [{"record_id": f"hybrid_{job}", "created": False}]
