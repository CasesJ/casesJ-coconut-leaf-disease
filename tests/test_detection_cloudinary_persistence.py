"""Firestore/Cloudinary commit behavior for new detection uploads."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.infrastructure.cloudinary_storage import CloudinaryStorageError
from app.domain.detection import deduplicate_records, upload_verification_status
from app.services import detection_service as service


def _asset(kind):
    return {"provider": "cloudinary", "asset_id": uuid4().hex,
            "public_id": f"neuralcoco/{kind}/{uuid4().hex}",
            "resource_type": "image", "delivery_type": "authenticated",
            "version": 1, "format": "jpg", "content_type": "image/jpeg",
            "byte_size": 12, "sha256": "test"}


class MemoryFirestore:
    def __init__(self, mode="ok"):
        self.mode = mode
        self.record = None
        self.create_calls = 0

    def collection(self, name):
        return self

    def document(self, name):
        return self

    def create(self, record):
        self.create_calls += 1
        if self.mode == "commit_then_error":
            self.record = deepcopy(record)
            raise TimeoutError("response lost")
        if self.mode in {"fail", "read_error"}:
            raise TimeoutError("write outcome unknown")
        self.record = deepcopy(record)

    def get(self):
        if self.mode == "read_error":
            raise TimeoutError("read unavailable")
        return SimpleNamespace(exists=self.record is not None,
                               to_dict=lambda: deepcopy(self.record))


def _persist(monkeypatch, db, refs, deleted):
    monkeypatch.setattr(service, "_get_firestore", lambda: (db, True))
    monkeypatch.setattr(service, "save_upload_image_assets", lambda *args: {"image_assets": refs})
    monkeypatch.setattr(service, "delete_asset", lambda ref: deleted.append(ref) or True)
    monkeypatch.setattr(service, "invalidate_expert_records_cache", lambda: None)


def _call():
    return service.save_detection_record(
        user_id="farmer-a", email="farmer@example.test", record_id="record-1",
        detections=[{"class": "Bud Rot", "confidence": 0.8, "bbox": [10, 20, 110, 220]}],
        gps_data={"latitude": 7.1, "longitude": 125.6}, filename="leaf.jpg",
        source="upload", image_metadata={"gps_source": "exif"},
        original_bytes=b"source", annotated_image=None, is_offline=False,
        image_dimensions={"width": 200, "height": 400},
    )


def test_firestore_stores_one_cloudinary_original_and_normalized_annotation_metadata(monkeypatch):
    db, original = MemoryFirestore(), _asset("original")
    deleted = []
    _persist(monkeypatch, db, {"original": original}, deleted)
    assert _call() is True
    assert db.record["image_assets"] == {"original": original}
    assert db.record["image_dimensions"] == {"width": 200, "height": 400}
    assert db.record["annotation_format"] == "xyxy-normalized-v1"
    assert db.record["detections"][0]["bbox_normalized"] == [0.05, 0.05, 0.55, 0.55]
    assert db.record["detections"][0]["bbox"] == [10, 20, 110, 220]
    assert "image_url" not in db.record and "secure_url" not in str(db.record)
    assert deleted == []


def test_healthy_upload_is_automatically_approved():
    assert upload_verification_status(
        [{"class": "Healthy", "confidence": 0.53}], "upload"
    ) == "verified"


def test_verified_ambiguous_firestore_commit_is_success(monkeypatch):
    db, deleted = MemoryFirestore("commit_then_error"), []
    refs = {"original": _asset("original")}
    _persist(monkeypatch, db, refs, deleted)
    assert _call() is True
    assert db.record["image_assets"] == refs
    assert deleted == []


def test_confirmed_absent_firestore_write_cleans_only_new_assets(monkeypatch):
    db, deleted = MemoryFirestore("fail"), []
    refs = {"original": _asset("original")}
    _persist(monkeypatch, db, refs, deleted)
    with pytest.raises(service.DetectionPersistenceError):
        _call()
    assert deleted == list(refs.values())


def test_uncertain_firestore_read_retains_assets_and_never_saves_locally(monkeypatch):
    db, deleted = MemoryFirestore("read_error"), []
    refs = {"original": _asset("original")}
    _persist(monkeypatch, db, refs, deleted)
    with pytest.raises(service.DetectionPersistenceError, match="uncertain"):
        _call()
    assert deleted == []


def test_firestore_unavailable_prevents_cloudinary_upload_or_sqlite_fallback(monkeypatch):
    uploads = []
    monkeypatch.setattr(service, "_get_firestore", lambda: (None, False))
    monkeypatch.setattr(service, "save_upload_image_assets", lambda *args:
                        uploads.append(args) or pytest.fail("Must check Firestore before uploading"))
    with pytest.raises(service.DetectionPersistenceError, match="Firestore is unavailable"):
        _call()
    assert uploads == []


def test_cloudinary_failure_is_not_a_local_only_detection(monkeypatch):
    db = MemoryFirestore()
    monkeypatch.setattr(service, "_get_firestore", lambda: (db, True))
    monkeypatch.setattr(service, "save_upload_image_assets", lambda *args:
                        (_ for _ in ()).throw(CloudinaryStorageError("upload failed")))
    with pytest.raises(service.DetectionPersistenceError, match="Cloudinary"):
        _call()
    assert db.create_calls == 0


def test_idempotent_retry_reuses_the_existing_firestore_record(monkeypatch):
    db, deleted = MemoryFirestore(), []
    reference = _asset("original")
    db.record = {"id": "record-1", "upload_idempotency_key": "a" * 16,
                 "image_assets": {"original": reference}}
    uploads = []
    monkeypatch.setattr(service, "_get_firestore", lambda: (db, True))
    monkeypatch.setattr(service, "save_upload_image_assets", lambda *args:
                        uploads.append(args) or pytest.fail("Retry must not upload a second asset"))

    assert service.save_detection_record(
        user_id="farmer-a", email="farmer@example.test", record_id="record-1",
        detections=[], gps_data={}, filename="leaf.jpg", source="upload",
        image_metadata={}, original_bytes=b"source", annotated_image=None,
        is_offline=False, idempotency_key="a" * 16,
    ) is True
    assert db.create_calls == 0
    assert uploads == []


def test_deduplicating_a_newer_sqlite_cache_keeps_firestore_asset_references():
    reference = _asset("original")
    local = {"id": "record-1", "user_id": "farmer-a", "timestamp": "2026-10-23T10:00:01Z",
             "filename": "leaf.jpg", "source": "upload"}
    firestore = {"id": "record-1", "user_id": "farmer-a", "timestamp": "2026-10-23T10:00:00Z",
                 "filename": "leaf.jpg", "source": "upload", "image_assets": {"original": reference}}
    merged = deduplicate_records([local, firestore])
    assert len(merged) == 1
    assert merged[0]["timestamp"] == local["timestamp"]
    assert merged[0]["image_assets"]["original"] == reference
