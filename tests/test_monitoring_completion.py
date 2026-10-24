"""Recovery confirmation preserves evidence and is safe to repeat."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from firebase_admin import firestore

from app.services import monitoring_service as service


@pytest.fixture
def completion_store(monkeypatch):
    data = {"id": "case-1", "farmer_id": "farmer-1", "tree_id": "TREE-1234567890",
            "baseline_detection_id": "upload-1", "disease": "Bud Rot",
            "latest_review_status": "expert_confirmed", "next_follow_up_date": "2030-01-01"}
    writes = []

    class Ref:
        def get(self, transaction=None):
            return SimpleNamespace(exists=bool(data), to_dict=lambda: deepcopy(data))

        def collection(self, name):
            assert name == "completion_events"
            return self

        def document(self, name):
            return self

    ref = Ref()

    class Transaction:
        def update(self, target, changes):
            assert target is ref
            data.update(deepcopy(changes))
            writes.append(("update", deepcopy(changes)))

        def create(self, target, changes):
            writes.append(("event", deepcopy(changes)))

    monkeypatch.setattr(service, "_case_ref", lambda farmer, case: ref)
    monkeypatch.setattr(service, "_firestore", lambda: SimpleNamespace(transaction=Transaction))
    monkeypatch.setattr(firestore, "transactional", lambda operation: operation)
    return data, writes


def test_completion_persists_expert_confirmation_and_preserves_history(completion_store):
    data, writes = completion_store
    original = deepcopy(data)
    result = service.finish_case("farmer-1", "case-1", "expert-1")
    assert result["monitoring_status"] == "finish"
    assert result["health_status"] == "healthy"
    assert result["finished_by_expert_uid"] == "expert-1"
    assert result["finished_at"]
    assert all(result[key] == value for key, value in original.items())
    assert [kind for kind, _ in writes] == ["update", "event"]
    assert service.finish_case("farmer-1", "case-1", "expert-2") == result
    assert len(writes) == 2


@pytest.mark.parametrize("farmer,case", [("other", "case-1"), ("farmer-1", "other")])
def test_completion_rejects_mismatched_case_owner(completion_store, farmer, case):
    _, writes = completion_store
    assert service.finish_case(farmer, case, "expert-1") is None
    assert not writes


def test_completion_missing_case_does_not_write(completion_store):
    data, writes = completion_store
    data.clear()
    assert service.finish_case("farmer-1", "case-1", "expert-1") is None
    assert not writes
