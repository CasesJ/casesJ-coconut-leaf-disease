"""Tests for the farmer-entered treatment application history."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.api import monitoring
from app.core.security import verify_firebase_token
from app.main import app
from app.services import monitoring_service as service


@pytest.fixture
def client(monkeypatch):
    app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "farmer-a", "email": "farmer@example.com"}
    monkeypatch.setattr(monitoring.store, "get_case", lambda farmer_id, case_id: {
        "id": case_id,
        "farmer_id": farmer_id,
        "tree_id": "tree-1",
        "baseline_detection_id": "upload-1",
        "treatment_record_id": "recommendation-1",
        "disease": "Bud Rot",
    } if (farmer_id, case_id) == ("farmer-a", "case-1") else None)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _application_payload(**changes):
    payload = {
        "treatment_performed": "Pruned affected fronds and applied approved fungicide",
        "application_date": (datetime.now(ZoneInfo("Asia/Manila")).date() - timedelta(days=1)).isoformat(),
        "notes": "Applied after the morning inspection.",
    }
    payload.update(changes)
    return payload


def test_farmer_can_create_treatment_application_with_server_audit_fields(client, monkeypatch):
    persisted = []
    monkeypatch.setattr(monitoring.store, "save_treatment_application",
                        lambda farmer_id, case_id, record, schedule: persisted.append((farmer_id, case_id, record, schedule)))

    response = client.post("/monitoring/cases/case-1/treatment-applications", json=_application_payload())

    assert response.status_code == 201, response.text
    created = response.json()
    assert created["case_id"] == "case-1"
    assert created["farmer_id"] == "farmer-a"
    assert created["recorded_by_farmer_uid"] == "farmer-a"
    assert created["treatment_performed"] == "Pruned affected fronds and applied approved fungicide"
    assert created["notes"] == "Applied after the morning inspection."
    expected_due = (datetime.fromisoformat(created["application_date"]) + timedelta(days=6)).date().isoformat()
    assert created["first_follow_up_due_date"] == expected_due
    assert created["follow_up_interval_days"] == 6
    assert created["created_at"].endswith("+08:00")
    assert created["id"]
    assert created["created_at"]
    assert len(persisted) == 1
    assert persisted[0][:3] == ("farmer-a", "case-1", created)
    assert persisted[0][3]["schedule_type"] == "post_treatment"
    assert persisted[0][3]["due_date"] == expected_due


def test_treatment_application_rejects_foreign_and_expert_access(client, monkeypatch):
    saved = []
    monkeypatch.setattr(monitoring.store, "save_treatment_application", lambda *args: saved.append(args))

    app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "farmer-b"}
    assert client.post("/monitoring/cases/case-1/treatment-applications", json=_application_payload()).status_code == 404

    app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "expert-1", "role": "expert"}
    assert client.post("/monitoring/cases/case-1/treatment-applications", json=_application_payload()).status_code == 403
    assert saved == []


def test_treatment_application_validates_date_and_persistence_failures(client, monkeypatch):
    saved = []
    monkeypatch.setattr(monitoring.store, "save_treatment_application", lambda *args: saved.append(args))
    future = (datetime.now(ZoneInfo("Asia/Manila")).date() + timedelta(days=1)).isoformat()
    assert client.post("/monitoring/cases/case-1/treatment-applications",
                       json=_application_payload(application_date=future)).status_code == 422
    assert client.post("/monitoring/cases/case-1/treatment-applications",
                       json=_application_payload(treatment_performed="   ")).status_code == 422
    assert saved == []

    def unavailable(*_args):
        raise RuntimeError("Firestore unavailable")

    monkeypatch.setattr(monitoring.store, "save_treatment_application", unavailable)
    response = client.post("/monitoring/cases/case-1/treatment-applications", json=_application_payload())
    assert response.status_code == 503
    assert response.json()["detail"] == "Treatment application storage unavailable"


def test_multiple_treatment_applications_persist_and_return_in_chronological_order(monkeypatch):
    documents = {}

    class Snapshot:
        def __init__(self, path, data):
            self.path = path
            self.data = data
            self.exists = data is not None

        def to_dict(self):
            return dict(self.data)

    class Ref:
        def __init__(self, path=""):
            self.path = path

        def collection(self, name):
            return Ref(f"{self.path}/{name}")

        def document(self, name):
            return Ref(f"{self.path}/{name}")

        def get(self):
            data = documents.get(self.path)
            return Snapshot(self.path, data) if data is not None else Snapshot(self.path, {})

        def create(self, data):
            assert self.path not in documents
            documents[self.path] = dict(data)

        def update(self, data):
            documents.setdefault(self.path, {}).update(data)

        def stream(self):
            prefix = self.path + "/"
            return [Snapshot(path, data) for path, data in documents.items()
                    if path.startswith(prefix) and "/" not in path[len(prefix):]]

    class Batch:
        def __init__(self):
            self.actions = []

        def create(self, ref, data):
            self.actions.append((ref, data))

        def update(self, ref, data):
            self.actions.append((ref, data, "update"))

        def commit(self):
            for action in self.actions:
                ref, data = action[:2]
                if len(action) == 3:
                    ref.update(data)
                else:
                    ref.create(data)

    class Firestore(Ref):
        def batch(self):
            return Batch()

    documents["/users/farmer-a/monitoring_cases/case-1"] = {
        "id": "case-1", "farmer_id": "farmer-a", "follow_up_interval_days": 6,
    }
    monkeypatch.setattr(service, "_firestore", lambda: Firestore())
    later = {
        "id": "application-later", "case_id": "case-1", "farmer_id": "farmer-a",
        "treatment_performed": "Second application", "application_date": "2026-10-20",
        "notes": None, "created_at": "2026-10-20T10:00:00+00:00",
        "recorded_by_farmer_uid": "farmer-a",
    }
    earlier = {
        "id": "application-earlier", "case_id": "case-1", "farmer_id": "farmer-a",
        "treatment_performed": "First application", "application_date": "2026-10-18",
        "notes": "Applied as directed", "created_at": "2026-10-18T11:00:00+00:00",
        "recorded_by_farmer_uid": "farmer-a",
    }

    service.save_treatment_application("farmer-a", "case-1", later)
    service.save_treatment_application("farmer-a", "case-1", earlier)
    history = service.list_treatment_applications("farmer-a", "case-1")

    assert len(documents) == 3  # The case summary plus two append-only application records.
    assert [record["id"] for record in history] == ["application-earlier", "application-later"]
    assert [record["application_date"] for record in history] == ["2026-10-18", "2026-10-20"]
    assert all(record["farmer_id"] == "farmer-a" and record["case_id"] == "case-1" for record in history)


def test_overview_includes_application_history_without_changing_recommendation(monkeypatch):
    case = {
        "id": "case-1", "farmer_id": "farmer-a", "baseline_detection_id": "upload-1",
        "treatment_record_id": "recommendation-1",
        "baseline_image_reference": {"detection_id": "upload-1"},
    }
    treatment = {"id": "recommendation-1", "recommendation_snapshot": {"recommendations": {"treatment": "PCA guidance"}}}

    class Snapshot:
        def __init__(self, data):
            self.exists = data is not None
            self._data = data

        def to_dict(self):
            return dict(self._data) if self._data else None

    class Ref:
        def collection(self, _name):
            return self

        def document(self, _name):
            return self

        def get(self):
            return Snapshot(treatment)

    monkeypatch.setattr(service, "get_case", lambda *_args: case)
    monkeypatch.setattr(service, "_firestore", lambda: Ref())
    monkeypatch.setattr(service, "list_follow_ups", lambda *_args: [])
    monkeypatch.setattr(service, "list_treatment_applications", lambda *_args: [{
        "id": "application-1", "treatment_performed": "Actual application",
        "application_date": "2026-10-18", "farmer_id": "farmer-a", "case_id": "case-1",
    }])
    monkeypatch.setattr(service, "list_planned_treatments", lambda *_args: [])
    monkeypatch.setattr(service, "list_follow_up_schedules", lambda *_args: [])

    overview = service.get_overview("farmer-a", "case-1")

    assert overview["treatment"]["recommendation_snapshot"]["recommendations"]["treatment"] == "PCA guidance"
    assert overview["treatment_applications"][0]["treatment_performed"] == "Actual application"
    assert overview["planned_treatments"] == []
    assert overview["follow_up_schedules"] == []
