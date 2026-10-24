"""Offline access-control and historical media resolver tests."""
from io import BytesIO
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
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


def _valid_jpeg_bytes():
    stream = BytesIO()
    Image.new("RGB", (8, 8), "green").save(stream, format="JPEG")
    return stream.getvalue()


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
    image_bytes = _valid_jpeg_bytes()
    monkeypatch.setattr(detection_service, "find_canonical_record_for_user", lambda uid, rid: {
        "id": rid, "user_id": uid, "image_assets": {"annotated": _cloudinary_ref("annotated")}})
    monkeypatch.setattr(media_service, "download_asset", lambda asset: (image_bytes, "image/jpeg"))
    assert http.get("/expert/media/detections/farmer-a/rec-2/annotated").status_code == 403
    actor.update(uid="expert-1", role="expert")
    response = http.get("/expert/media/detections/farmer-a/rec-2/annotated")
    assert response.status_code == 200 and response.content == image_bytes
    assert response.headers["content-type"] == "image/jpeg"
    Image.open(BytesIO(response.content)).verify()


def test_new_annotated_media_route_renders_boxes_from_metadata_on_demand(client, monkeypatch):
    http, _ = client
    image_bytes = _valid_jpeg_bytes()
    reference = _cloudinary_ref("original")
    record = {
        "id": "record-new",
        "user_id": "farmer-a",
        "image_assets": {"original": reference},
        "image_dimensions": {"width": 8, "height": 8},
        "detections": [{
            "class": "Bud Rot", "confidence": 0.82,
            "bbox": [1, 1, 6, 6], "bbox_normalized": [0.125, 0.125, 0.75, 0.75],
        }],
    }
    downloads = []
    monkeypatch.setattr(detection_service, "find_canonical_record_for_user",
                        lambda uid, rid: dict(record) if uid == "farmer-a" and rid == "record-new" else None)
    monkeypatch.setattr(media_service, "download_asset",
                        lambda asset: downloads.append(asset) or (image_bytes, "image/jpeg"))

    response = http.get("/media/detections/record-new/annotated")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert downloads == [reference]
    assert response.content != image_bytes
    with Image.open(BytesIO(response.content)) as annotated:
        assert annotated.size == (8, 8)


@pytest.mark.parametrize(
    ("role", "actor_update", "records_url"),
    [
        ("farmer", {}, "/detections/my-records"),
        ("expert", {"uid": "expert-1", "role": "expert"}, "/expert/records?status=all"),
    ],
)
def test_new_cloudinary_upload_round_trips_through_farmer_and_expert_history(
    client, monkeypatch, role, actor_update, records_url,
):
    http, actor = client
    actor.update(actor_update)
    reference = _cloudinary_ref("original")
    image_bytes = _valid_jpeg_bytes()
    record = {
        "id": "upload_farmera_20261023_a1b2c3d4",
        "user_id": "farmer-a",
        "email": "farmer@example.test",
        "timestamp": "2026-10-23T10:00:00Z",
        "source": "upload",
        "detections": [{
            "class": "Bud Rot", "confidence": 0.82, "bbox": [1, 1, 6, 6],
            "bbox_normalized": [0.125, 0.125, 0.75, 0.75],
        }],
        "image_dimensions": {"width": 8, "height": 8},
        "image_assets": {"original": reference},
    }
    if role == "farmer":
        monkeypatch.setattr(detection_service, "get_user_records", lambda uid, limit: [dict(record)])
    else:
        from app.api import expert as expert_api
        monkeypatch.setattr(expert_api, "get_all_user_records", lambda limit: [dict(record)])
        monkeypatch.setattr(detection_service, "get_all_user_records", lambda limit: [dict(record)])
    monkeypatch.setattr(
        detection_service,
        "find_canonical_record_for_user",
        lambda uid, rid: dict(record) if uid == record["user_id"] and rid == record["id"] else None,
    )

    def download(asset):
        assert asset == reference
        return image_bytes, "image/jpeg"

    monkeypatch.setattr(media_service, "download_asset", download)
    listed = http.get(records_url)
    assert listed.status_code == 200
    presented = listed.json()["records"][0]
    if role == "farmer":
        expected_url = f"/media/detections/{record['id']}/original"
    else:
        expected_url = f"/expert/media/detections/farmer-a/{record['id']}/original"
    assert presented["image_url"] == expected_url
    assert presented["annotated_image_url"] == expected_url.rsplit("/", 1)[0] + "/annotated"
    annotated = http.get(presented["annotated_image_url"])
    assert annotated.status_code == 200
    assert annotated.headers["content-type"] == "image/jpeg"
    Image.open(BytesIO(annotated.content)).verify()
    assert presented["detections"][0]["bbox_normalized"] == [0.125, 0.125, 0.75, 0.75]
    assert presented["image_dimensions"] == {"width": 8, "height": 8}
    assert "image_assets" not in presented

    response = http.get(expected_url)
    assert response.status_code == 200
    assert response.content == image_bytes
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, no-store"
    Image.open(BytesIO(response.content)).verify()


def test_obsolete_local_storage_paths_are_not_presented_as_available_images(monkeypatch, tmp_path):
    monkeypatch.setattr(media_service.config, "UPLOAD_IMAGE_DIR", tmp_path / "removed-uploads")
    monkeypatch.setattr(media_service.config, "ANNOTATED_IMAGE_DIR", tmp_path / "removed-annotated")
    record = {
        "id": "old-record",
        "user_id": "farmer-a",
        "image_url": "/static/uploads/old-record.jpg",
        "original_storage_path": "static/uploads/old-record.jpg",
        "image_path": "C:/old-sqlite-data/uploads/old-record.jpg",
    }

    presented = media_service.present_detection_record(record)

    assert "image_url" not in presented


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


def test_historical_firebase_media_reference_still_gets_a_protected_route():
    record = {
        "id": "rec",
        "user_id": "farmer-a",
        "image_url": "https://firebasestorage.googleapis.com/v0/b/legacy-bucket/o/"
        + quote("detections/rec/original.jpg", safe="")
        + "?alt=media&token=historic-token",
    }

    presented = media_service.present_detection_record(record)

    assert presented["image_url"] == "/media/detections/rec/original"
    assert "historic-token" not in str(presented)


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


def test_baseline_image_uses_snapshot_without_loading_overview(client, monkeypatch):
    http, actor = client
    actor.update(uid="expert-1", role="expert")
    reference = _cloudinary_ref()
    case = {"id": "case-1", "farmer_id": "farmer-a", "baseline_detection_id": "rec-1",
            "baseline_image_reference": {"image_assets": {"original": reference}}}
    monkeypatch.setattr(monitoring_service, "get_case", lambda *args: case)
    monkeypatch.setattr(monitoring_service, "get_overview", lambda *args: pytest.fail("Full overview should not load"))
    monkeypatch.setattr(detection_service, "find_canonical_record_for_user", lambda *args: pytest.fail("Snapshot is sufficient"))
    monkeypatch.setattr(media_service, "download_asset", lambda asset: (b"snapshot", "image/jpeg"))
    response = http.get("/expert/media/monitoring/farmer-a/case-1/baseline/original")
    assert response.status_code == 200 and response.content == b"snapshot"


def test_baseline_recovers_delivery_only_snapshot_from_owned_detection(client, monkeypatch):
    http, _ = client
    case = {"id": "case-1", "farmer_id": "farmer-a", "baseline_detection_id": "rec-1",
            "baseline_image_reference": {"image_url": "/media/detections/rec-1/original"}}
    monkeypatch.setattr(monitoring_service, "get_case", lambda *args: case)
    monkeypatch.setattr(monitoring_service, "get_overview", lambda *args: pytest.fail("Full overview should not load"))
    monkeypatch.setattr(detection_service, "find_canonical_record_for_user", lambda uid, rid: {
        "id": rid, "user_id": uid, "image_assets": {"original": _cloudinary_ref()}})
    monkeypatch.setattr(media_service, "download_asset", lambda asset: (b"recovered", "image/jpeg"))
    response = http.get("/media/monitoring/case-1/baseline/original")
    assert response.status_code == 200 and response.content == b"recovered"
