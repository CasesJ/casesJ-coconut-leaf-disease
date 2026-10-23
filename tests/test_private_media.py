"""Offline access-control and historical media resolver tests."""
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.api import media as media_api
from app.core.security import verify_verified_firebase_token
from app.infrastructure.cloudinary_storage import CloudinaryStorageError
from app.main import app
from app.services import detection_service, media_service, monitoring_service


@pytest.fixture
def client(monkeypatch):
    actor = {"uid": "farmer-a"}
    app.dependency_overrides[verify_verified_firebase_token] = lambda: dict(actor)
    yield TestClient(app), actor
    app.dependency_overrides.clear()


def _cloudinary_ref(kind="original"):
    return {"provider": "cloudinary", "asset_id": uuid4().hex,
            "public_id": f"neuralcoco/detection-{kind}/{uuid4().hex}",
            "resource_type": "image", "delivery_type": "authenticated",
            "version": 1, "format": "jpg", "content_type": "image/jpeg",
            "byte_size": 9, "sha256": "test"}


def test_farmer_can_read_only_their_cloudinary_detection_image(client, monkeypatch):
    http, actor = client
    ref = _cloudinary_ref()
    monkeypatch.setattr(detection_service, "find_canonical_record_for_user", lambda uid, rid: {
        "id": rid, "user_id": "farmer-a", "image_assets": {"original": ref}})
    monkeypatch.setattr(media_service, "download_asset", lambda asset: (b"private", "image/jpeg"))
    response = http.get("/media/detections/rec-1/original")
    assert response.status_code == 200 and response.content == b"private"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"

    actor["uid"] = "farmer-b"
    response = http.get("/media/detections/rec-1/original")
    assert response.status_code == 404


def test_expert_route_requires_expert_and_binds_the_owner(client, monkeypatch):
    http, actor = client
    monkeypatch.setattr(detection_service, "find_canonical_record_for_user", lambda uid, rid: {
        "id": rid, "user_id": uid, "image_assets": {"annotated": _cloudinary_ref("annotated")}})
    monkeypatch.setattr(media_service, "download_asset", lambda asset: (b"annotated", "image/jpeg"))
    assert http.get("/expert/media/detections/farmer-a/rec-2/annotated").status_code == 403
    actor.update(uid="expert-1", role="expert")
    response = http.get("/expert/media/detections/farmer-a/rec-2/annotated")
    assert response.status_code == 200 and response.content == b"annotated"


def test_media_route_rejects_missing_authentication(client):
    http, _ = client
    app.dependency_overrides.clear()
    response = http.get("/media/detections/rec-1/original")
    assert response.status_code == 401
    assert http.get("/static/uploads/rec.jpg").status_code == 401
    assert http.get("/uploads/rec.jpg").status_code == 401


def test_record_presentation_hides_cloudinary_and_firebase_delivery_details():
    secret_url = "https://firebasestorage.googleapis.com/v0/b/f/o/path?token=historic-token"
    record = {"id": "rec", "user_id": "farmer-a", "image_url": secret_url,
              "annotated_image_url": "https://res.cloudinary.com/private/image/authenticated/x.jpg",
              "image_assets": {"original": _cloudinary_ref()}}
    presented = media_service.present_detection_record(record)
    assert presented["image_url"] == "/media/detections/rec/original"
    assert presented["annotated_image_url"] == "/media/detections/rec/annotated"
    assert "image_assets" not in presented
    assert "historic-token" not in str(presented)
    assert "res.cloudinary.com" not in str(presented)


def test_historical_firebase_storage_reference_is_read_through_admin_sdk(monkeypatch):
    from firebase_admin import storage
    content = b"legacy image"
    requested = []

    class Bucket:
        name = "legacy-bucket"
        def blob(self, name):
            requested.append(name)
            return type("Blob", (), {"download_as_bytes": lambda self, **kwargs: content})()

    monkeypatch.setattr(storage, "bucket", lambda: Bucket())
    record = {"id": "rec", "image_url":
              "https://firebasestorage.googleapis.com/v0/b/legacy-bucket/o/"
              + quote("detections/rec/original.jpg", safe="") + "?alt=media&token=private"}
    assert media_service.read_detection_image(record, "original") == (content, "image/jpeg")
    assert requested == ["detections/rec/original.jpg"]


def test_firebase_reference_cannot_select_an_arbitrary_object_in_the_bucket(monkeypatch):
    from firebase_admin import storage
    monkeypatch.setattr(storage, "bucket", lambda: type("Bucket", (), {
        "name": "legacy-bucket",
        "blob": lambda self, name: pytest.fail(f"Unexpected object read: {name}"),
    })())
    url = "https://firebasestorage.googleapis.com/v0/b/legacy-bucket/o/" + quote("private/admin.json", safe="")
    with pytest.raises(media_service.MediaNotFound):
        media_service.read_detection_image({"id": "rec", "image_url": url}, "original")


def test_external_historical_urls_are_not_fetched(monkeypatch):
    monkeypatch.setattr("requests.get", lambda *args, **kwargs:
                        pytest.fail("Media resolver must not fetch arbitrary URLs"))
    with pytest.raises(media_service.MediaNotFound):
        media_service.read_detection_image({"id": "rec", "image_url": "https://attacker.test/x.jpg"}, "original")


def test_legacy_local_image_uses_owner_checked_static_route(client, monkeypatch, tmp_path):
    http, actor = client
    original_root = tmp_path / "uploads"
    annotated_root = tmp_path / "annotated"
    original_root.mkdir()
    annotated_root.mkdir()
    (original_root / "rec.jpg").write_bytes(b"local-private")
    monkeypatch.setattr(media_service.config, "UPLOAD_IMAGE_DIR", original_root)
    monkeypatch.setattr(media_service.config, "ANNOTATED_IMAGE_DIR", annotated_root)
    record = {"id": "rec", "user_id": "farmer-a", "image_url": "/static/uploads/rec.jpg"}
    monkeypatch.setattr(detection_service, "get_all_user_records", lambda limit: [record])
    response = http.get("/static/uploads/rec.jpg")
    assert response.status_code == 200 and response.content == b"local-private"
    assert http.get("/uploads/rec.jpg").status_code == 200
    monkeypatch.setattr(detection_service, "find_canonical_record_for_user", lambda uid, rid: {
        "id": "rec", "user_id": "farmer-a", "image_path": str(original_root / "rec.jpg")})
    assert http.get("/media/detections/rec/original").content == b"local-private"
    actor["uid"] = "farmer-b"
    assert http.get("/static/uploads/rec.jpg").status_code == 404


def test_cloudinary_download_error_is_not_replaced_with_local_fallback(monkeypatch):
    record = {"id": "rec", "image_assets": {"original": _cloudinary_ref()},
              "image_url": "/static/uploads/rec.jpg"}
    monkeypatch.setattr(media_service, "download_asset", lambda asset:
                        (_ for _ in ()).throw(CloudinaryStorageError("unavailable")))
    monkeypatch.setattr(media_service, "_local_file", lambda *args: pytest.fail("No local fallback"))
    with pytest.raises(media_service.MediaUnavailable):
        media_service.read_detection_image(record, "original")


def test_monitoring_media_enforces_farmer_owner_and_expert_role(client, monkeypatch):
    http, actor = client
    case = {"id": "case-1", "farmer_id": "farmer-a"}
    follow_up = {"id": "follow-1", "case_id": "case-1", "farmer_id": "farmer-a",
                 "image_assets": {"original": _cloudinary_ref()}}
    monkeypatch.setattr(monitoring_service, "get_case", lambda uid, case_id: case if uid == "farmer-a" else None)
    monkeypatch.setattr(monitoring_service, "get_follow_up", lambda uid, case_id, follow_id:
                        follow_up if uid == "farmer-a" and follow_id == "follow-1" else None)
    monkeypatch.setattr(media_service, "download_asset", lambda asset: (b"evidence", "image/jpeg"))
    response = http.get("/media/monitoring/case-1/follow-ups/follow-1/original")
    assert response.status_code == 200 and response.content == b"evidence"
    actor.update(uid="farmer-b")
    assert http.get("/media/monitoring/case-1/follow-ups/follow-1/original").status_code == 404
    actor.update(uid="expert-1", role="expert")
    response = http.get("/expert/media/monitoring/farmer-a/case-1/follow-ups/follow-1/original")
    assert response.status_code == 200
