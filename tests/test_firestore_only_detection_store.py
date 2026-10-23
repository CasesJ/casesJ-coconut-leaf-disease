"""Offline checks that detection workflows use canonical Firestore only."""
import asyncio
from copy import deepcopy
import pytest
from fastapi.responses import StreamingResponse

from app.api import reports
from app.services import export_service
from app.services import detection_service, monitoring_service


class Snapshot:
    def __init__(self, reference, value):
        self.reference = reference
        self._value = deepcopy(value)
        self.exists = value is not None
        self.id = reference.id

    def to_dict(self):
        return deepcopy(self._value)


class Collection:
    def __init__(self, db, path):
        self.db, self.path = db, path

    @property
    def parent(self):
        return Document(self.db, self.path[:-1]) if len(self.path) > 1 else None

    def document(self, doc_id):
        return Document(self.db, (*self.path, str(doc_id)))

    def order_by(self, *args, **kwargs):
        return Query(self.db, path=self.path)

    def limit(self, maximum):
        return Query(self.db, path=self.path).limit(maximum)

    def stream(self):
        prefix = (*self.path,)
        return [Snapshot(Document(self.db, key), value)
                for key, value in self.db.data.items()
                if len(key) == len(prefix) + 1 and key[:len(prefix)] == prefix]


class Query:
    def __init__(self, db, collection_name=None, path=None):
        self.db, self.collection_name, self.path = db, collection_name, path
        self.maximum = None

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, maximum):
        self.maximum = maximum
        return self

    def stream(self):
        if self.collection_name:
            keys = [key for key in self.db.data if len(key) >= 4 and key[-2] == self.collection_name]
        else:
            keys = [key for key in self.db.data
                    if len(key) == len(self.path) + 1 and key[:-1] == self.path]
        keys.sort(key=lambda key: str(self.db.data[key].get("timestamp") or ""), reverse=True)
        if self.maximum is not None:
            keys = keys[:self.maximum]
        return [Snapshot(Document(self.db, key), self.db.data[key]) for key in keys]


class Document:
    def __init__(self, db, path):
        self.db, self._path = db, tuple(path)
        self.id = self._path[-1]

    @property
    def path(self):
        return "/".join(self._path)

    @property
    def parent(self):
        return Collection(self.db, self._path[:-1])

    def collection(self, name):
        return Collection(self.db, (*self._path, name))

    def get(self):
        return Snapshot(self, self.db.data.get(self._path))

    def create(self, value):
        if self._path in self.db.data:
            raise ValueError("already exists")
        self.db.data[self._path] = deepcopy(value)

    def update(self, value):
        if self._path not in self.db.data:
            raise KeyError("not found")
        self.db.data[self._path].update(deepcopy(value))


class Batch:
    def __init__(self, db):
        self.db, self.operations = db, []

    def set(self, reference, value):
        self.operations.append(("set", reference, deepcopy(value)))

    def update(self, reference, value):
        self.operations.append(("update", reference, deepcopy(value)))

    def commit(self):
        for operation, reference, value in self.operations:
            if operation == "set":
                self.db.data[reference._path] = value
            else:
                self.db.data[reference._path].update(value)


class Firestore:
    def __init__(self):
        self.data = {}

    def collection(self, name):
        return Collection(self, (name,))

    def collection_group(self, name):
        return Query(self, collection_name=name)

    def batch(self):
        return Batch(self)


@pytest.fixture
def firestore(monkeypatch):
    db = Firestore()
    monkeypatch.setattr(detection_service, "_get_firestore", lambda: (db, True))
    detection_service.invalidate_expert_records_cache()
    yield db
    detection_service.invalidate_expert_records_cache()


def _record(record_id, owner="farmer-a", **updates):
    return {
        "id": record_id, "user_id": owner, "timestamp": "2026-10-23T10:00:00Z",
        "source": "upload", "detections": [{"class": "Bud Rot", "confidence": 0.8}],
        **updates,
    }


def _put(db, *path, value):
    db.data[tuple(path)] = deepcopy(value)


def test_farmer_history_reads_only_the_owners_canonical_firestore_collection(firestore, monkeypatch):
    _put(firestore, "users", "farmer-a", "detections", "record-a", value=_record("record-a"))
    _put(firestore, "users", "farmer-a", "uploads", "legacy-a", value=_record("legacy-a"))
    _put(firestore, "users", "farmer-b", "detections", "foreign", value=_record("foreign", "farmer-b"))
    import firebase_admin.db as rtdb
    monkeypatch.setattr(rtdb, "reference", lambda *_args, **_kwargs:
                        pytest.fail("Detection history must not query Realtime Database"))

    records = detection_service.get_user_records("farmer-a")

    assert [record["id"] for record in records] == ["record-a"]
    assert records[0]["storage_backend"] == "firestore"


def test_new_detection_and_mutation_write_only_canonical_firestore(firestore, monkeypatch):
    assets = {"original": {"provider": "cloudinary", "asset_id": "asset-1"}}
    monkeypatch.setattr(detection_service, "save_upload_image_assets",
                        lambda *_args: {"image_assets": assets})

    assert detection_service.save_detection_record(
        user_id="farmer-a", email="farmer@example.test", record_id="new-detection",
        detections=[{"class": "Bud Rot", "confidence": 0.8, "bbox": [10, 20, 110, 220]}],
        gps_data={"latitude": 7.1, "longitude": 125.6}, filename="leaf.jpg",
        source="upload", image_metadata={"gps_source": "exif"}, original_bytes=b"png",
        annotated_image=None, is_offline=False,
        image_dimensions={"width": 200, "height": 400},
    )
    path = ("users", "farmer-a", "detections", "new-detection")
    assert firestore.data[path]["image_assets"] == assets
    assert firestore.data[path]["image_dimensions"] == {"width": 200, "height": 400}
    assert firestore.data[path]["detections"][0]["bbox_normalized"] == [0.05, 0.05, 0.55, 0.55]
    assert firestore.data[path]["gps_data"]["latitude"] == 7.1
    assert "users/farmer-a/uploads/new-detection" not in firestore.data
    assert detection_service.update_record_everywhere(
        "farmer-a", "new-detection", {"verification_status": "verified"}
    )
    assert firestore.data[path]["verification_status"] == "verified"


def test_firestore_unavailability_fails_closed_without_cloudinary_or_local_fallback(monkeypatch):
    uploads = []
    monkeypatch.setattr(detection_service, "_get_firestore", lambda: (None, False))
    monkeypatch.setattr(detection_service, "save_upload_image_assets",
                        lambda *args: uploads.append(args))
    with pytest.raises(detection_service.DetectionPersistenceError, match="Firestore is unavailable"):
        detection_service.save_detection_record(
            "farmer-a", "farmer@example.test", "new", [], {}, "leaf.jpg", "upload",
            {}, b"image", None, False,
        )
    assert uploads == []
    with pytest.raises(detection_service.DetectionStoreUnavailableError):
        detection_service.get_user_records("farmer-a")


def test_expert_queue_reads_only_the_firestore_detections_collection_group(firestore):
    _put(firestore, "users", "farmer-a", "detections", "new", value=_record("new"))
    _put(firestore, "users", "farmer-a", "uploads", "legacy", value=_record("legacy"))

    records = detection_service.get_all_user_records(limit=20)

    assert [record["id"] for record in records] == ["new"]
    assert records[0]["user_id"] == "farmer-a"


def test_treatment_baseline_is_loaded_from_the_canonical_firestore_detection(firestore, monkeypatch):
    baseline = _record(
        "baseline", verification_status="verified", verified_by_role="expert",
        verified_by="expert@example.test", verified_by_uid="expert-1",
        verified_at="2026-10-23T09:00:00Z", gps_data={"latitude": 7.1, "longitude": 125.6},
        image_assets={"original": {"provider": "cloudinary", "asset_id": "baseline-image"}},
        recommendation_snapshot={"disease": "Bud Rot", "recommendations": {"treatment": "Prune affected tissue"}},
    )
    _put(firestore, "users", "farmer-a", "detections", "baseline", value=baseline)
    monkeypatch.setattr(monitoring_service, "_firestore", lambda: firestore)

    case = monitoring_service.create_case("farmer-a", "tree-1", "baseline", None, "expert-1")

    assert case["baseline_detection_id"] == "baseline"
    assert case["baseline_image_reference"]["image_assets"]["original"]["asset_id"] == "baseline-image"
    assert case["baseline_gps_snapshot"]["latitude"] == 7.1
    assert any(key[:3] == ("users", "farmer-a", "monitoring_cases") for key in firestore.data)


def test_pdf_report_uses_the_authenticated_users_firestore_history(firestore, monkeypatch):
    _put(firestore, "users", "farmer-a", "detections", "report-record",
         value=_record("report-record"))
    monkeypatch.setattr(reports, "get_user_records", detection_service.get_user_records)
    seen = {}

    def build_pdf(records, email):
        seen["record_ids"] = [record["id"] for record in records]
        seen["email"] = email
        return b"%PDF-offline-test"

    monkeypatch.setattr(reports, "build_executive_user_records_pdf", build_pdf)
    response = asyncio.run(reports.download_my_records_pdf({
        "uid": "farmer-a", "email": "farmer@example.test",
    }))

    assert isinstance(response, StreamingResponse)
    assert seen == {"record_ids": ["report-record"], "email": "farmer@example.test"}


def test_expert_csv_export_uses_firestore_detections(firestore, monkeypatch):
    _put(firestore, "users", "farmer-a", "detections", "csv-record",
         value=_record("csv-record", email="farmer@example.test", tree_id="tree-1",
                       gps_data={"latitude": 7.1, "longitude": 125.6}))
    monkeypatch.setattr(export_service, "get_all_user_records", detection_service.get_all_user_records)

    output = export_service.generate_detections_csv()

    assert "Bud Rot,0.8000,low,tree-1" in output
    assert "farmer@example.test" in output
    assert "125.6" in output
