"""Regression coverage for planned and completed treatment lifecycle records."""

from copy import deepcopy
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.api import monitoring
from app.core.security import verify_firebase_token
from app.main import app
from app.services import monitoring_service as service

_PH = ZoneInfo("Asia/Manila")


@pytest.fixture
def client(monkeypatch):
    case = {
        "id": "case-1", "farmer_id": "farmer-a", "tree_id": "tree-1",
        "baseline_detection_id": "upload-1", "treatment_record_id": "recommendation-1",
        "disease": "Bud Rot", "follow_up_interval_days": 5,
        "next_follow_up_date": (_today() + timedelta(days=30)).isoformat(),
    }
    app.dependency_overrides[verify_firebase_token] = lambda: {
        "uid": "farmer-a", "email": "farmer@example.com",
    }
    monkeypatch.setattr(monitoring.store, "get_case", lambda farmer, case_id:
                        deepcopy(case) if (farmer, case_id) == ("farmer-a", "case-1") else None)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _today():
    return datetime.now(_PH).date()


def test_farmer_can_schedule_future_treatment_without_application(client, monkeypatch):
    plans, applications = [], []
    monkeypatch.setattr(monitoring.store, "save_planned_treatment", lambda *args: plans.append(args))
    monkeypatch.setattr(monitoring.store, "save_treatment_application", lambda *args: applications.append(args))
    planned_date = _today() + timedelta(days=4)

    response = client.post("/monitoring/cases/case-1/planned-treatments", json={
        "planned_date": planned_date.isoformat(), "notes": "After the rain clears",
    })

    assert response.status_code == 201, response.text
    planned = response.json()
    assert planned["planned_date"] == planned_date.isoformat()
    assert planned["status"] == "scheduled"
    assert planned["created_by_farmer_uid"] == "farmer-a"
    assert planned["created_at"].endswith("+08:00")
    assert plans[0][2] == planned
    assert applications == []


def test_planned_treatment_rejects_today_and_past_dates(client, monkeypatch):
    saved = []
    monkeypatch.setattr(monitoring.store, "save_planned_treatment", lambda *args: saved.append(args))
    for planned_date in (_today(), _today() - timedelta(days=1)):
        response = client.post("/monitoring/cases/case-1/planned-treatments", json={
            "planned_date": planned_date.isoformat(),
        })
        assert response.status_code == 422
    app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "farmer-b"}
    assert client.post("/monitoring/cases/case-1/planned-treatments", json={
        "planned_date": (_today() + timedelta(days=1)).isoformat(),
    }).status_code == 404
    app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "expert-1", "role": "expert"}
    assert client.post("/monitoring/cases/case-1/planned-treatments", json={
        "planned_date": (_today() + timedelta(days=1)).isoformat(),
    }).status_code == 403
    assert saved == []


def test_delayed_actual_application_schedules_from_actual_date_and_preserves_progression(client, monkeypatch):
    saved = []
    monkeypatch.setattr(monitoring.store, "save_treatment_application", lambda *args: saved.append(args))
    actual_date = _today() - timedelta(days=14)
    future_date = _today() + timedelta(days=1)

    future = client.post("/monitoring/cases/case-1/treatment-applications", json={
        "treatment_performed": "Applied fungicide", "application_date": future_date.isoformat(),
    })
    assert future.status_code == 422
    assert saved == []

    response = client.post("/monitoring/cases/case-1/treatment-applications", json={
        "treatment_performed": "Pruned affected fronds and applied fungicide",
        "application_date": actual_date.isoformat(), "notes": "Recorded two weeks late",
    })

    assert response.status_code == 201, response.text
    application = response.json()
    expected_due = actual_date + timedelta(days=5)
    assert application["application_date"] == actual_date.isoformat()
    assert application["first_follow_up_due_date"] == expected_due.isoformat()
    assert application["follow_up_interval_days"] == 5
    assert application["created_at"].endswith("+08:00")
    assert saved[0][2] == application
    assert saved[0][3]["schedule_type"] == "post_treatment"
    assert saved[0][3]["due_date"] == expected_due.isoformat()
    assert expected_due < _today()  # The post-treatment check is overdue because application was delayed in the log.
    assert client.get("/monitoring/cases/case-1").json()["next_follow_up_date"] == (
        _today() + timedelta(days=30)
    ).isoformat()


def test_repeated_applications_get_distinct_post_treatment_schedules(client, monkeypatch):
    saved = []
    monkeypatch.setattr(monitoring.store, "save_treatment_application", lambda *args: saved.append(args))
    dates = [_today() - timedelta(days=9), _today() - timedelta(days=2)]

    responses = [client.post("/monitoring/cases/case-1/treatment-applications", json={
        "treatment_performed": f"Application {index + 1}", "application_date": day.isoformat(),
    }) for index, day in enumerate(dates)]

    assert [response.status_code for response in responses] == [201, 201]
    applications = [response.json() for response in responses]
    schedules = [entry[3] for entry in saved]
    assert applications[0]["id"] != applications[1]["id"]
    assert schedules[0]["id"] != schedules[1]["id"]
    assert [schedule["due_date"] for schedule in schedules] == [
        (day + timedelta(days=5)).isoformat() for day in dates
    ]
    assert [schedule["treatment_application_id"] for schedule in schedules] == [
        application["id"] for application in applications
    ]


def test_application_can_be_linked_to_a_scheduled_treatment(client, monkeypatch):
    plan = {"id": "plan-1", "farmer_id": "farmer-a", "case_id": "case-1", "status": "scheduled"}
    saved = []
    monkeypatch.setattr(monitoring.store, "get_planned_treatment", lambda *args: plan)
    monkeypatch.setattr(monitoring.store, "save_treatment_application", lambda *args: saved.append(args))

    response = client.post("/monitoring/cases/case-1/treatment-applications", json={
        "treatment_performed": "Applied scheduled treatment", "application_date": _today().isoformat(),
        "planned_treatment_id": "plan-1",
    })

    assert response.status_code == 201, response.text
    assert response.json()["planned_treatment_id"] == "plan-1"
    assert saved[0][2]["planned_treatment_id"] == "plan-1"


def test_overdue_planned_treatment_is_derived_without_rewriting_history(monkeypatch):
    stored = {"id": "plan-1", "case_id": "case-1", "farmer_id": "farmer-a",
              "planned_date": (_today() - timedelta(days=2)).isoformat(), "status": "scheduled"}

    class Snapshot:
        def to_dict(self):
            return deepcopy(stored)

    class Ref:
        def collection(self, _name):
            return self

        def stream(self):
            return [Snapshot()]

    class CaseRef:
        def collection(self, _name):
            return Ref()

    monkeypatch.setattr(service, "_case_ref", lambda *_args: CaseRef())

    planned = service.list_planned_treatments("farmer-a", "case-1")

    assert planned[0]["is_overdue"] is True
    assert stored["status"] == "scheduled"
    assert "is_overdue" not in stored


def test_firestore_keeps_plans_applications_and_inspection_schedules_separate(monkeypatch):
    documents = {
        "/users/farmer-a/monitoring_cases/case-1": {
            "id": "case-1", "farmer_id": "farmer-a", "follow_up_interval_days": 6,
            "next_follow_up_date": "2030-01-15", "treatment_status": "awaiting_treatment",
        },
        "/users/farmer-a/treatment_records/recommendation-1": {
            "id": "recommendation-1", "recommendation_snapshot": {"recommendations": {"treatment": "Keep immutable"}},
        },
    }

    class Snapshot:
        def __init__(self, path):
            self.exists = path in documents
            self.data = documents.get(path)

        def to_dict(self):
            return deepcopy(self.data) if self.data is not None else None

    class Ref:
        def __init__(self, path=""):
            self.path = path

        def collection(self, name):
            return Ref(f"{self.path}/{name}")

        def document(self, name):
            return Ref(f"{self.path}/{name}")

        def get(self):
            return Snapshot(self.path)

        def create(self, data):
            assert self.path not in documents
            documents[self.path] = deepcopy(data)

        def update(self, data):
            assert self.path in documents
            documents[self.path].update(deepcopy(data))

        def stream(self):
            prefix = self.path + "/"
            return [Snapshot(path) for path in documents
                    if path.startswith(prefix) and "/" not in path[len(prefix):]]

    class Batch:
        def __init__(self):
            self.actions = []

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

    monkeypatch.setattr(service, "_firestore", lambda: Firestore())
    planned = {
        "id": "planned-1", "case_id": "case-1", "farmer_id": "farmer-a",
        "planned_date": "2030-01-10", "notes": None, "status": "scheduled",
        "created_at": "2026-10-23T12:00:00+08:00", "created_by_farmer_uid": "farmer-a",
    }
    service.save_planned_treatment("farmer-a", "case-1", planned)
    assert "/users/farmer-a/monitoring_cases/case-1/planned_treatments/planned-1" in documents
    assert not any("/treatment_applications/" in path for path in documents)

    application = {
        "id": "application-1", "case_id": "case-1", "farmer_id": "farmer-a",
        "treatment_performed": "Applied treatment", "application_date": "2030-01-10",
        "planned_treatment_id": "planned-1", "first_follow_up_due_date": "2030-01-16",
        "follow_up_interval_days": 6, "created_at": "2026-10-23T12:05:00+08:00",
    }
    schedule = {
        "id": "post-check-1", "case_id": "case-1", "farmer_id": "farmer-a",
        "schedule_type": "post_treatment", "due_date": "2030-01-16",
        "follow_up_interval_days": 6, "treatment_application_id": "application-1",
        "created_at": "2026-10-23T12:05:00+08:00", "created_by_uid": "farmer-a",
        "created_by_role": "farmer", "status": "scheduled",
    }
    service.save_treatment_application("farmer-a", "case-1", application, schedule)

    case = documents["/users/farmer-a/monitoring_cases/case-1"]
    assert case["treatment_status"] == "treatment_applied"
    assert case["next_follow_up_date"] == "2030-01-15"  # Untreated progression schedule is untouched.
    assert case["next_post_treatment_follow_up_date"] == "2030-01-16"
    assert documents["/users/farmer-a/monitoring_cases/case-1/planned_treatments/planned-1"]["status"] == "applied"
    assert "/users/farmer-a/monitoring_cases/case-1/treatment_applications/application-1" in documents
    assert "/users/farmer-a/monitoring_cases/case-1/follow_up_schedules/post-check-1" in documents
    assert documents["/users/farmer-a/treatment_records/recommendation-1"]["recommendation_snapshot"]["recommendations"]["treatment"] == "Keep immutable"
