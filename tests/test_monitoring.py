"""Monitoring access, upload validation, and evidence persistence."""

import sys
import zlib
from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from io import BytesIO
from PIL import Image

from app.api import monitoring
from app.core.security import verify_firebase_token
from app.main import app
from app.services import monitoring_service as service


@pytest.fixture
def client(monkeypatch):
    app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "farmer-a", "email": "farmer@example.com"}
    monkeypatch.setattr(monitoring.store, "get_case", lambda farmer, case: {
        "id": case, "farmer_id": farmer, "tree_id": "tree-1",
        "baseline_detection_id": "upload-1", "treatment_record_id": "treatment-1",
        "disease": "Bud Rot",
    } if farmer == "farmer-a" and case == "case-1" else None)

    async def allowed(*args, **kwargs):
        return None

    monkeypatch.setattr(monitoring.limiter, "rolling", allowed)
    monkeypatch.setattr(monitoring.limiter, "image_quota_24h", allowed)
    monkeypatch.setattr(monitoring.store, "follow_up_upload_allowance",
                        lambda *_: {"limit": 3, "used": 0, "remaining": 3})
    uploads, deleted = [], []

    def upload_asset(content, content_type, category, **_kwargs):
        reference = {"provider": "cloudinary", "asset_id": uuid4().hex,
                     "public_id": f"neuralcoco/{category}/{uuid4().hex}",
                     "resource_type": "image", "delivery_type": "authenticated",
                     "version": 1, "format": "png" if content_type == "image/png" else "jpg",
                     "content_type": content_type, "byte_size": len(content), "sha256": "test"}
        uploads.append((category, content, content_type, reference))
        return reference

    monkeypatch.setattr(monitoring, "upload_asset", upload_asset)
    monkeypatch.setattr(monitoring, "delete_asset", lambda ref: deleted.append(ref) or True)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _image():
    success, encoded = cv2.imencode(".jpg", np.zeros((16, 16, 3), dtype=np.uint8))
    assert success
    return encoded.tobytes()


def _png_image(size=(64, 48)):
    output = BytesIO()
    pixels = np.random.default_rng(8).integers(0, 256, (size[1], size[0], 3), dtype=np.uint8)
    Image.fromarray(pixels).save(output, "PNG")
    return output.getvalue()


def _upload(client, data, mime="image/jpeg", case_id="case-1"):
    return client.post(f"/monitoring/cases/{case_id}/follow-ups",
                       files={"file": ("leaf.jpg", data, mime)})


def _pillow_image(image_format="JPEG", size=(64, 48), *, orientation=None):
    output = BytesIO()
    image = Image.new("RGB", size, "green")
    options = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[274] = orientation
        options["exif"] = exif
    image.save(output, format=image_format, **options)
    return output.getvalue()


@pytest.mark.parametrize("data,mime,status", [
    (b"not an image", "image/jpeg", 400),
    (b"", "image/jpeg", 400),
])
def test_invalid_uploads(client, data, mime, status):
    assert _upload(client, data, mime).status_code == status


def test_oversize_upload(client, monkeypatch):
    monkeypatch.setattr(monitoring, "MAX_IMAGE_UPLOAD_BYTES", 4)
    assert _upload(client, _image()).status_code == 413


def test_follow_up_accepts_valid_smartphone_jpeg_despite_incorrect_browser_mime():
    source = _pillow_image("JPEG", (4032, 3024))
    image, content_type, stored, dimensions = monitoring._decode_follow_up(source, "application/octet-stream")
    assert content_type == "image/jpeg"
    assert stored == source
    assert dimensions == {"width": 4032, "height": 3024}
    assert image is None  # Follow-ups do not allocate a detector raster.


def test_follow_up_accepts_valid_png():
    image, content_type, stored, dimensions = monitoring._decode_follow_up(
        _pillow_image("PNG"), "image/png")
    assert content_type == "image/png"
    assert stored.startswith(b"\x89PNG")
    assert dimensions == {"width": 64, "height": 48}
    assert image is None


def test_follow_up_rejects_truncated_png_without_decoding_a_full_raster():
    with pytest.raises(monitoring.HTTPException) as error:
        monitoring._decode_follow_up(_pillow_image("PNG")[:-12], "image/png")
    assert error.value.status_code == 400


def test_follow_up_applies_exif_orientation_without_reencoding_original():
    source = _pillow_image("JPEG", (64, 48), orientation=6)
    image, content_type, stored, dimensions = monitoring._decode_follow_up(source, "image/jpeg")
    assert content_type == "image/jpeg"
    assert stored == source
    assert dimensions == {"width": 48, "height": 64}
    assert image is None


def test_follow_up_accepts_dji_mpo_primary_frame_with_metadata_preserved():
    output = BytesIO()
    primary = Image.new("RGB", (64, 48), "green")
    secondary = Image.new("RGB", (64, 48), "blue")
    primary.save(output, format="MPO", save_all=True, append_images=[secondary])

    image, content_type, stored, dimensions = monitoring._decode_follow_up(
        output.getvalue(), "image/jpeg")

    assert content_type == "image/jpeg"
    assert stored.startswith(b"\xff\xd8") and stored.endswith(b"\xff\xd9")
    assert dimensions == {"width": 64, "height": 48}
    assert image is None


def test_follow_up_accepts_image_at_configured_pixel_limit(monkeypatch):
    monkeypatch.setattr(monitoring, "MAX_IMAGE_UPLOAD_PIXELS", 1_000)
    image, _content_type, _stored, dimensions = monitoring._decode_follow_up(
        _pillow_image("JPEG", (40, 25)), "image/jpeg")
    assert dimensions == {"width": 40, "height": 25}
    assert image is None


def test_follow_up_reports_actual_dimensions_when_pixel_limit_is_exceeded(monkeypatch):
    monkeypatch.setattr(monitoring, "MAX_IMAGE_UPLOAD_PIXELS", 50_000_000)
    # A valid PNG header advertising 56 MP; Pillow reads the dimensions before
    # any pixel allocation, which is the production safety boundary too.
    oversized = bytearray(_pillow_image("PNG", (1, 1)))
    oversized[16:20] = (8000).to_bytes(4, "big")
    oversized[20:24] = (7000).to_bytes(4, "big")
    oversized[29:33] = zlib.crc32(oversized[12:29]).to_bytes(4, "big")
    with pytest.raises(monitoring.HTTPException) as error:
        monitoring._decode_follow_up(bytes(oversized), "image/png")
    assert error.value.status_code == 400
    assert "8000 x 7000" in error.value.detail
    assert "50 megapixels" in error.value.detail


@pytest.mark.parametrize("source, message", [
    (b"not an image", "cannot identify image file"),
    (_pillow_image("GIF"), "Unsupported image format"),
])
def test_follow_up_rejects_corrupt_and_unsupported_images(source, message):
    with pytest.raises(monitoring.HTTPException) as error:
        monitoring._decode_follow_up(source, "image/jpeg")
    assert error.value.status_code == 400
    assert message in error.value.detail


def test_invalid_preliminary_status_is_rejected_before_inference(client, monkeypatch):
    monkeypatch.setitem(sys.modules, "model", SimpleNamespace(detector=SimpleNamespace(
        predict=lambda *args, **kwargs: pytest.fail("Inference must not run"))))
    response = client.post("/monitoring/cases/case-1/follow-ups",
        files={"file": ("leaf.jpg", _image(), "image/jpeg")},
        data={"preliminary_status": "recovered"})
    assert response.status_code == 422


def test_missing_and_foreign_cases_never_infer(client, monkeypatch):
    monkeypatch.setitem(sys.modules, "model", SimpleNamespace(detector=SimpleNamespace(
        predict=lambda *args, **kwargs: pytest.fail("Inference must not run"))))
    assert _upload(client, _image(), case_id="missing").status_code == 404
    app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "farmer-b"}
    assert _upload(client, _image()).status_code == 404


def test_expert_cannot_upload_as_farmer(client):
    app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "expert-1", "role": "expert"}
    assert _upload(client, _image()).status_code == 403


def test_successful_follow_up_stores_evidence_without_invoking_inference(client, monkeypatch):
    source = _image()
    saved = []
    uploads = []
    monkeypatch.setattr(monitoring, "run_image_inference",
                        lambda *_: pytest.fail("Follow-up must not infer"), raising=False)

    def fake_upload(content, content_type, category, **_kwargs):
        uploads.append((category, content, content_type))
        return {"provider": "cloudinary", "asset_id": uuid4().hex,
                "public_id": f"neuralcoco/{category}/{uuid4().hex}",
                "resource_type": "image", "delivery_type": "authenticated",
                "version": 1, "format": "jpg", "content_type": content_type,
                "byte_size": len(content), "sha256": "test"}

    monkeypatch.setattr(monitoring, "upload_asset", fake_upload)
    monkeypatch.setattr(monitoring, "reference_metadata",
                        lambda *args: pytest.fail("Omitted whole-tree image must skip metadata processing"))
    monkeypatch.setattr(monitoring.store, "save_follow_up", lambda farmer, case, record: saved.append(record))
    response = _upload(client, source)
    assert response.status_code == 201, response.text
    assert len(uploads) == 1
    assert uploads[0] == ("monitoring-original", source, "image/jpeg")
    assert saved[0]["predictions"] == []
    assert saved[0]["image_dimensions"] == {"width": 16, "height": 16}
    assert saved[0]["annotation_format"] is None
    assert saved[0]["baseline_detection_id"] == "upload-1"
    assert saved[0]["review_status"] == "pending_expert_review"
    assert saved[0]["gps_data"] is None
    assert saved[0]["tree_reference"] is None
    assert saved[0]["image_url"] and saved[0]["annotated_image_url"] is None
    assert saved[0]["original_storage_path"] is None
    assert saved[0]["annotated_storage_path"] is None
    assert set(saved[0]["image_assets"]) == {"original"}
    assert "api.cloudinary.com" not in response.text
    assert "severity" not in saved[0] and "recovery" not in saved[0]


def test_follow_up_does_not_depend_on_inference_availability(client, monkeypatch):
    upload_attempts = []
    saved = []
    monkeypatch.setattr(monitoring, "upload_asset",
                        lambda *args, **kwargs: upload_attempts.append(args) or {
                            "provider": "cloudinary", "asset_id": uuid4().hex,
                            "public_id": "test", "content_type": "image/jpeg"})
    monkeypatch.setattr(monitoring.store, "save_follow_up",
                        lambda *args: saved.append(args))

    response = _upload(client, _image())

    assert response.status_code == 201
    assert len(upload_attempts) == 1
    assert saved[0][2]["predictions"] == []


def test_follow_up_and_whole_tree_pngs_use_safe_storage_representation(client, monkeypatch):
    from app.infrastructure.imaging import image_utils
    monkeypatch.setattr(image_utils.config, "DETECTION_STORAGE_MAX_BYTES", 100_000)
    saved = []
    monkeypatch.setattr(monitoring.store, "save_follow_up", lambda *_args: saved.append(_args[2]))
    source = _png_image((400, 400))
    response = client.post("/monitoring/cases/case-1/follow-ups", files={
        "file": ("leaf.png", source, "image/png"),
        "tree_reference_file": ("tree.png", source, "image/png"),
    })
    assert response.status_code == 201, response.text
    record = saved[0]
    assert record["image_assets"]["original"]["storage_representation"] == "png_to_jpeg_storage_copy"
    assert record["image_assets"]["original"]["source_content_type"] == "image/png"
    assert record["tree_reference"]["image_asset"]["storage_representation"] == "png_to_jpeg_storage_copy"
    assert record["tree_reference"]["image_asset"]["stored_content_type"] == "image/jpeg"


def test_original_storage_failure_does_not_save_follow_up(client, monkeypatch):
    uploaded = []
    deleted = []

    def partial_upload(content, content_type, category, **_kwargs):
        uploaded.append((category, content_type))
        if category == "monitoring-original":
            raise RuntimeError("original upload unavailable")
        return {"provider": "cloudinary", "asset_id": uuid4().hex,
                "public_id": f"neuralcoco/{category}/{uuid4().hex}",
                "resource_type": "image", "delivery_type": "authenticated",
                "version": 1, "format": "jpg", "content_type": content_type,
                "byte_size": len(content), "sha256": "test"}

    monkeypatch.setattr(monitoring, "upload_asset", partial_upload)
    monkeypatch.setattr(monitoring, "delete_asset", lambda ref: deleted.append(ref) or True)
    monkeypatch.setattr(monitoring.store, "save_follow_up",
                        lambda *args: pytest.fail("No partial follow-up may be persisted"))

    response = _upload(client, _image())

    assert response.status_code == 503
    assert [entry[0] for entry in uploaded] == ["monitoring-original"]
    assert deleted == []


def test_case_creation_requires_expert(client, monkeypatch):
    payload = {"farmer_id": "farmer-a", "tree_id": "tree-1",
               "baseline_detection_id": "upload-1",
               "next_follow_up_date": (date.today() + timedelta(days=3)).isoformat()}
    response = client.post("/expert/monitoring/cases", json=payload)
    assert response.status_code == 403
    app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "expert-1", "role": "expert"}
    monkeypatch.setattr(monitoring.store, "create_case", lambda *args: {"id": "case-1"})
    audit_events = []
    monkeypatch.setattr(monitoring, "append_expert_audit_event",
                        lambda **event: audit_events.append(event))
    assert client.post("/expert/monitoring/cases", json=payload).status_code == 201
    assert audit_events[0]["action"] == "create_monitoring_case"
    assert audit_events[0]["target"] == {
        "user_id": "farmer-a", "record_id": "upload-1", "tree_id": None,
    }
    payload.pop("tree_id")
    assert client.post("/expert/monitoring/cases", json=payload).status_code == 201


def test_expert_follow_up_read_and_schedule_are_protected(client, monkeypatch):
    path = "/expert/monitoring/cases/farmer-a/case-1"
    assert client.get(path + "/follow-ups").status_code == 403
    app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "expert-1", "role": "expert"}
    monkeypatch.setattr(monitoring.store, "list_follow_ups", lambda *args: [{"id": "follow-1"}])
    assert client.get(path + "/follow-ups").json() == {"follow_ups": [{"id": "follow-1"}]}
    monkeypatch.setattr(monitoring.store, "update_next_follow_up", lambda *args: {
        "next_follow_up_date": args[2], "schedule_updated_by_expert_uid": args[3]})
    next_day = (date.today() + timedelta(days=3)).isoformat()
    result = client.put(path + "/schedule", json={"next_follow_up_date": next_day})
    assert result.status_code == 200
    assert result.json()["schedule_updated_by_expert_uid"] == "expert-1"


def test_case_rejects_foreign_baseline(monkeypatch):
    monkeypatch.setattr(service, "find_record_for_user", lambda *args: {"user_id": "farmer-b"})
    with pytest.raises(ValueError, match="not found for farmer"):
        service.create_case("farmer-a", "tree-1", "upload-1", "2030-01-01", "expert-1")


def test_case_rejects_unverified_baseline(monkeypatch):
    monkeypatch.setattr(service, "find_record_for_user", lambda *args: {
        "user_id": "farmer-a", "source": "upload", "verification_status": "pending_verification",
        "detections": [{"class": "Bud Rot", "confidence": 0.9}],
    })
    with pytest.raises(ValueError, match="expert verification"):
        service.create_case("farmer-a", "tree-1", "upload-1", "2030-01-01", "expert-1")


def test_case_and_treatment_are_written_together(monkeypatch):
    monkeypatch.setattr(service, "find_record_for_user", lambda *args: {
        "user_id": "farmer-a", "source": "upload", "verification_status": "verified",
        "verified_by_role": "expert", "verified_at": "2026-10-20T00:00:00Z",
        "timestamp": "2026-10-19T00:00:00Z", "filename": "baseline.jpg",
        "image_url": "https://firebasestorage.googleapis.com/v0/b/test/o/detections%2Fupload-1%2Foriginal.jpg?alt=media",
        "annotated_image_url": "https://firebasestorage.googleapis.com/v0/b/test/o/detections%2Fupload-1%2Fannotated.jpg?alt=media",
        "detections": [{"class": "Bud Rot", "confidence": 0.9, "bbox": [1, 2, 3, 4]}],
        "recommendation_source": "expert_override",
        "recommendation_snapshot": {"disease": "Bud Rot", "model_used": "Expert Override",
                                    "source": "expert_override", "recommendation_scope": "record",
                                    "recommendations": {"treatment": "Care plan"}},
    })
    writes = []

    class Ref:
        def __init__(self, path=""):
            self.path = path

        def collection(self, name):
            return Ref(self.path + "/" + name)

        def document(self, name):
            return Ref(self.path + "/" + name)

    class Batch:
        def set(self, ref, data):
            writes.append((ref.path, data))

        def commit(self):
            writes.append("committed")

    class Firestore(Ref):
        def batch(self):
            return Batch()

    from app.services import tree_identity_service as identity
    def commit_identity(farmer, baseline, tree, write):
        batch = Batch()
        result = write(batch, tree)
        batch.commit()
        return result
    monkeypatch.setattr(identity, "commit_care_identity", commit_identity)
    monkeypatch.setattr(service, "_firestore", lambda: Firestore())
    case = service.create_case("farmer-a", "tree-1", "upload-1", "2030-01-01", "expert-1")
    assert len(writes) == 4 and writes[-1] == "committed"
    assert "/users/farmer-a/treatment_records/" in writes[0][0]
    assert "/users/farmer-a/monitoring_cases/" in writes[1][0]
    assert "/follow_up_schedules/" in writes[1][0]
    assert "/users/farmer-a/monitoring_cases/" in writes[2][0]
    assert writes[0][1]["id"] == case["treatment_record_id"]
    assert case["baseline_detection_id"] == "upload-1"
    assert case["next_follow_up_date"] == "2030-01-01"
    assert case["baseline_image_reference"]["image_url"].endswith("original.jpg?alt=media")
    assert case["baseline_image_reference"]["original_storage_path"] == "detections/upload-1/original.jpg"
    assert case["baseline_prediction_snapshot"] == [{"class": "Bud Rot", "confidence": 0.9, "bbox": [1, 2, 3, 4]}]
    assert case["baseline_recommendation_provenance"] == {
        "source": "expert_override", "model_used": "Expert Override",
        "recommendation_scope": "record", "record_recommendation_source": "expert_override",
    }
    assert writes[0][1]["recommendation_provenance"] == case["baseline_recommendation_provenance"]


def test_legacy_case_with_missing_baseline_reference_uses_detection_record(monkeypatch):
    class Snapshot:
        exists = False

        @staticmethod
        def to_dict():
            return None

    class Ref:
        def collection(self, _name):
            return self

        def document(self, _name):
            return self

        @staticmethod
        def get():
            return Snapshot()

    monkeypatch.setattr(service, "_firestore", lambda: Ref())
    monkeypatch.setattr(service, "get_case", lambda *_args: {
        "id": "case-1", "farmer_id": "farmer-a", "baseline_detection_id": "upload-1",
        "treatment_record_id": "treatment-1",
    })
    monkeypatch.setattr(service, "find_record_for_user", lambda *_args: {
        "id": "upload-1", "user_id": "farmer-a", "filename": "legacy.jpg",
        "timestamp": "2026-10-19T00:00:00Z", "image_url": "https://storage.test/legacy-original",
        "annotated_image_url": "https://storage.test/legacy-annotated",
        "detections": [{"class": "Bud Rot", "confidence": 0.9}],
    })
    monkeypatch.setattr(service, "list_follow_ups", lambda *_args: [])
    monkeypatch.setattr(service, "list_treatment_applications", lambda *_args: [])
    monkeypatch.setattr(service, "list_planned_treatments", lambda *_args: [])
    monkeypatch.setattr(service, "list_follow_up_schedules", lambda *_args: [])

    overview = service.get_overview("farmer-a", "case-1")

    assert overview["baseline"]["reference_source"] == "legacy_detection_record"
    assert overview["baseline"]["image_url"] == "https://storage.test/legacy-original"
    assert overview["baseline"]["predictions"] == [{"class": "Bud Rot", "confidence": 0.9}]
    assert overview["planned_treatments"] == []
    assert overview["follow_up_schedules"] == []


def test_case_snapshot_is_used_even_if_detection_record_changes(monkeypatch):
    class Snapshot:
        exists = False

        @staticmethod
        def to_dict():
            return None

    class Ref:
        def collection(self, _name):
            return self

        def document(self, _name):
            return self

        @staticmethod
        def get():
            return Snapshot()

    monkeypatch.setattr(service, "_firestore", lambda: Ref())
    monkeypatch.setattr(service, "get_case", lambda *_args: {
        "id": "case-1", "farmer_id": "farmer-a", "baseline_detection_id": "upload-1",
        "treatment_record_id": "treatment-1",
        "baseline_image_reference": {
            "detection_id": "upload-1", "image_url": "https://storage.test/immutable-original",
            "annotated_image_url": "https://storage.test/immutable-annotated",
            "original_storage_path": "detections/upload-1/original.jpg",
            "filename": "baseline.jpg", "timestamp": "2026-10-19T00:00:00Z",
        },
        "baseline_prediction_snapshot": [{"class": "Bud Rot", "confidence": 0.9}],
        "baseline_recommendation_provenance": {"source": "expert_override"},
    })
    monkeypatch.setattr(service, "find_record_for_user",
                        lambda *_args: pytest.fail("New cases must not reread a mutable detection record"))
    monkeypatch.setattr(service, "list_follow_ups", lambda *_args: [])
    monkeypatch.setattr(service, "list_treatment_applications", lambda *_args: [])
    monkeypatch.setattr(service, "list_planned_treatments", lambda *_args: [])
    monkeypatch.setattr(service, "list_follow_up_schedules", lambda *_args: [])

    overview = service.get_overview("farmer-a", "case-1")

    assert overview["baseline"]["reference_source"] == "case_snapshot"
    assert overview["baseline"]["image_url"] == "https://storage.test/immutable-original"
    assert overview["baseline"]["predictions"] == [{"class": "Bud Rot", "confidence": 0.9}]
    assert overview["planned_treatments"] == []
    assert overview["follow_up_schedules"] == []


def test_complete_baseline_follow_up_expert_review(monkeypatch):
    """Exercise the HTTP workflow against one shared in-memory Firestore store."""
    client = TestClient(app)
    async def allowed(*args, **kwargs):
        return None
    monkeypatch.setattr(monitoring.limiter, "rolling", allowed)
    monkeypatch.setattr(monitoring.limiter, "image_quota_24h", allowed)
    documents = {}

    class Snapshot:
        def __init__(self, path, data):
            self.id = path.rsplit("/", 1)[-1]
            self.exists = data is not None
            self.data = data

        def to_dict(self):
            return dict(self.data) if self.data is not None else None

    class Ref:
        def __init__(self, path=""):
            self.path = path

        def collection(self, name):
            return Ref(f"{self.path}/{name}")

        def document(self, name):
            return Ref(f"{self.path}/{name}")

        def get(self, transaction=None):
            return Snapshot(self.path, documents.get(self.path))

        def set(self, data):
            documents[self.path] = dict(data)

        def create(self, data):
            assert self.path not in documents
            self.set(data)

        def update(self, data):
            assert self.path in documents
            documents[self.path].update(data)

        def order_by(self, field):
            return self

        def stream(self):
            prefix = self.path + "/"
            return [Snapshot(path, data) for path, data in documents.items()
                    if path.startswith(prefix) and "/" not in path[len(prefix):]]

    class Batch:
        def __init__(self):
            self.actions = []

        def set(self, ref, data):
            self.actions.append((ref.set, data))

        def create(self, ref, data):
            self.actions.append((ref.create, data))

        def update(self, ref, data):
            self.actions.append((ref.update, data))

        def commit(self):
            for action, data in self.actions:
                action(data)

    class Firestore(Ref):
        def batch(self):
            return Batch()

        def collection_group(self, name):
            return SimpleNamespace(stream=lambda: [Snapshot(path, data)
                for path, data in documents.items() if f"/{name}/" in path])

    fake_db = Firestore()
    monkeypatch.setattr(service, "_firestore", lambda: fake_db)
    baseline = {"id": "upload-1", "user_id": "farmer-a", "source": "upload",
                "timestamp": "2026-10-19T00:00:00Z", "image_url": "https://storage.test/baseline",
                "verification_status": "verified", "verified_by_role": "expert",
                "verified_at": "2026-10-20T00:00:00Z",
                "detections": [{"class": "Bud Rot", "confidence": 0.86}],
                "recommendation_snapshot": {"disease": "Bud Rot", "model_used": "Saved recommendation",
                                            "recommendations": {"treatment": "Recorded guidance"}}}
    monkeypatch.setattr(service, "find_record_for_user", lambda farmer, record: baseline)
    from app.services import tree_identity_service as identity
    documents["/users/farmer-a/detections/upload-1"] = baseline
    documents["/users/farmer-a/trees/tree-1"] = {"id": "tree-1", "farmer_id": "farmer-a"}
    def transaction(operation):
        batch = Batch()
        result = operation(batch)
        batch.commit()
        return result
    monkeypatch.setattr(identity, "_transaction", transaction)
    monkeypatch.setattr(service, "_follow_up_transaction", transaction)

    monkeypatch.setitem(sys.modules, "model", SimpleNamespace(detector=SimpleNamespace(
        predict=lambda image, conf, *, annotate=True: {"detections": [{"class": "Bud Rot", "confidence": 0.7}],
                                     "image": image})))
    def upload_asset(content, mime, category, **_kwargs):
        return {"provider": "cloudinary", "asset_id": uuid4().hex,
                "public_id": f"neuralcoco/{category}/{uuid4().hex}",
                "resource_type": "image", "delivery_type": "authenticated",
                "version": 1, "format": "jpg", "content_type": mime,
                "byte_size": len(content), "sha256": "test"}
    monkeypatch.setattr(monitoring, "upload_asset", upload_asset)

    app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "expert-1", "role": "expert"}
    created = client.post("/expert/monitoring/cases", json={"farmer_id": "farmer-a",
        "tree_id": "tree-1", "baseline_detection_id": "upload-1", "follow_up_interval_days": 5})
    assert created.status_code == 201, created.text
    case_id = created.json()["id"]
    treatment_id = created.json()["treatment_record_id"]
    assert documents[f"/users/farmer-a/treatment_records/{treatment_id}"]["recommendation_snapshot"]["recommendations"]["treatment"] == "Recorded guidance"

    app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "farmer-a"}
    initial_overview = client.get(f"/monitoring/cases/{case_id}/overview").json()
    assert initial_overview["case"]["treatment_status"] == "awaiting_treatment"
    assert initial_overview["planned_treatments"] == []
    assert initial_overview["treatment_applications"] == []
    assert initial_overview["treatment"]["recommendation_snapshot"]["recommendations"]["treatment"] == "Recorded guidance"

    uploaded = client.post(f"/monitoring/cases/{case_id}/follow-ups",
        files={"file": ("leaf.jpg", _image(), "image/jpeg")},
        data={"preliminary_status": "no_visible_change"})
    assert uploaded.status_code == 201, uploaded.text
    follow_up_id = uploaded.json()["id"]
    assert uploaded.json()["review_status"] == "pending_expert_review"
    assert uploaded.json()["preliminary_status"] == "no_visible_change"
    review_path = f"/expert/monitoring/cases/farmer-a/{case_id}/follow-ups/{follow_up_id}/review"
    assert client.put(review_path, json={"confirmed_outcome": "improving", "note": "Observed"}).status_code == 403

    app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "expert-1", "role": "expert"}
    assert client.put(review_path, json={"confirmed_outcome": "recovered", "note": "Observed"}).status_code == 422
    assert client.put(review_path, json={"confirmed_outcome": "improving", "note": " "}).status_code == 422
    review = client.put(review_path,
        json={"confirmed_outcome": "improving", "note": "Fewer visible lesions; inspect again."})
    assert review.status_code == 200, review.text
    assert review.json()["reviewed_by_expert_uid"] == "expert-1"
    assert review.json()["confirmed_outcome"] == "improving"
    assert review.json()["next_follow_up_date"] == documents[f"/users/farmer-a/monitoring_cases/{case_id}"]["next_follow_up_date"]
    assert documents[f"/users/farmer-a/monitoring_cases/{case_id}"]["latest_review_status"] == "expert_confirmed"

    app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "farmer-a"}
    progression_date = documents[f"/users/farmer-a/monitoring_cases/{case_id}"]["next_follow_up_date"]
    application = client.post(f"/monitoring/cases/{case_id}/treatment-applications", json={
        "treatment_performed": "Applied the expert-recommended fungicide",
        "application_date": monitoring._today_philippine().isoformat(),
    })
    assert application.status_code == 201, application.text
    application_data = application.json()
    assert application_data["first_follow_up_due_date"] == (
        date.fromisoformat(application_data["application_date"]) + timedelta(days=5)
    ).isoformat()
    assert documents[f"/users/farmer-a/monitoring_cases/{case_id}"]["next_follow_up_date"] == progression_date
    post_schedule = next(schedule for path, schedule in documents.items()
                         if path.startswith(f"/users/farmer-a/monitoring_cases/{case_id}/follow_up_schedules/")
                         and schedule.get("treatment_application_id") == application_data["id"])
    assert post_schedule["schedule_type"] == "post_treatment"

    post_inspections = []
    for _ in range(2):
        inspection = client.post(f"/monitoring/cases/{case_id}/follow-ups",
            files={"file": ("leaf.jpg", _image(), "image/jpeg")},
            data={"preliminary_status": "no_visible_change", "follow_up_schedule_id": post_schedule["id"]})
        assert inspection.status_code == 201, inspection.text
        post_inspections.append(inspection.json())
    blocked = client.post(f"/monitoring/cases/{case_id}/follow-ups",
        files={"file": ("leaf.jpg", _image(), "image/jpeg")})
    assert blocked.status_code == 409
    assert "limit of 3" in blocked.json()["detail"]
    assert post_inspections[0]["id"] != post_inspections[1]["id"]
    assert all(record["treatment_application_id"] == application_data["id"] for record in post_inspections)

    app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "expert-1", "role": "expert"}
    next_post_review = client.put(
        f"/expert/monitoring/cases/farmer-a/{case_id}/follow-ups/{post_inspections[-1]['id']}/review",
        json={"confirmed_outcome": "no_visible_change", "note": "Continue monitoring."})
    assert next_post_review.status_code == 200, next_post_review.text
    assert next_post_review.json()["follow_up_schedule_id"]

    app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "farmer-a"}
    overview = client.get(f"/monitoring/cases/{case_id}/overview")
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["baseline"]["image_url"] == f"/media/monitoring/{case_id}/baseline/original"
    assert body["baseline"]["thumbnail_url"] == f"/media/monitoring/{case_id}/baseline/thumbnail"
    assert body["treatment"]["recommendation_snapshot"]["recommendations"]["treatment"] == "Recorded guidance"
    assert body["follow_ups"][0]["confirmed_outcome"] == "improving"
    assert len(body["follow_ups"]) == 3
    assert len(body["treatment_applications"]) == 1
    assert len([schedule for schedule in body["follow_up_schedules"]
                if schedule["schedule_type"] == "post_treatment"]) == 2


def test_finish_requires_expert_and_returns_shared_status(client, monkeypatch):
    path = "/expert/monitoring/cases/farmer-a/case-1/finish"
    assert client.put(path).status_code == 403
    app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "expert-1", "role": "expert"}
    calls = []
    def finish(farmer, case, expert):
        calls.append((farmer, case, expert))
        return {"id": case, "farmer_id": farmer, "monitoring_status": "finish", "health_status": "healthy"}
    monkeypatch.setattr(service, "finish_case", finish)
    response = client.put(path)
    assert response.status_code == 200
    assert response.json()["health_status"] == "healthy"
    assert response.json()["monitoring_status"] == "finish"
    assert calls == [("farmer-a", "case-1", "expert-1")]
    monkeypatch.setattr(service, "finish_case", lambda *args: None)
    assert client.put(path).status_code == 404


def test_follow_up_limit_blocks_before_reading_or_uploading_image(client, monkeypatch):
    monkeypatch.setattr(service, "follow_up_upload_allowance",
                        lambda *_: {"limit": 3, "used": 3, "remaining": 0})
    monkeypatch.setattr(monitoring, "upload_asset", lambda *_: pytest.fail("No asset should upload"))
    response = _upload(client, b"not even an image")
    assert response.status_code == 409
    assert "3 follow-up image uploads" in response.json()["detail"]
