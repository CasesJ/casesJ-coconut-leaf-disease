"""Per-farmer, per-tree follow-up limits, including history and concurrent writes."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import RLock
from types import SimpleNamespace

import pytest

from app.services import monitoring_service as service


@pytest.fixture
def store(monkeypatch):
    documents = {}
    lock = RLock()

    class Ref:
        def __init__(self, path=""):
            self.path = path

        def collection(self, name):
            return Ref(f"{self.path}/{name}")

        document = collection

        def get(self, transaction=None):
            value = deepcopy(documents.get(self.path))
            return SimpleNamespace(exists=value is not None, to_dict=lambda: value)

    class Transaction:
        def set(self, ref, value):
            documents[ref.path] = deepcopy(value)

        def create(self, ref, value):
            assert ref.path not in documents
            self.set(ref, value)

        def update(self, ref, value):
            documents[ref.path].update(deepcopy(value))

    def transaction(operation):
        with lock:
            before = deepcopy(documents)
            try:
                return operation(Transaction())
            except Exception:
                documents.clear()
                documents.update(before)
                raise

    def cases(farmer):
        with lock:
            return [deepcopy(value) for path, value in documents.items()
                    if path.startswith(f"/users/{farmer}/monitoring_cases/") and path.count("/") == 4]

    def follow_ups(farmer, case):
        with lock:
            return [deepcopy(value) for path, value in documents.items()
                    if path.startswith(f"/users/{farmer}/monitoring_cases/{case}/follow_ups/")]

    monkeypatch.setattr(service, "_firestore", Ref)
    monkeypatch.setattr(service, "_follow_up_transaction", transaction)
    monkeypatch.setattr(service, "list_cases", cases)
    monkeypatch.setattr(service, "list_follow_ups", follow_ups)
    return documents


def case(store, farmer="farmer", tree="TREE-A", case_id="case-a"):
    store[f"/users/{farmer}/monitoring_cases/{case_id}"] = {
        "id": case_id, "farmer_id": farmer, "tree_id": tree}


def upload(number, farmer="farmer", tree="TREE-A", case_id="case-a"):
    service.save_follow_up(farmer, case_id, {
        "id": str(number), "farmer_id": farmer, "tree_id": tree,
        "case_id": case_id, "created_at": "2026-10-24T12:00:00+08:00"})


def test_three_uploads_allowed_fourth_rejected_without_changes(store):
    case(store)
    for number in range(3):
        upload(number)
    before = deepcopy(store)
    with pytest.raises(service.FollowUpLimitReached):
        upload(4)
    assert store == before
    assert service.follow_up_upload_allowance("farmer", "TREE-A") == {
        "limit": 3, "used": 3, "remaining": 0}


def test_historical_uploads_count_across_cases_for_same_tree(store):
    case(store)
    case(store, case_id="case-b")
    for number in range(2):
        store[f"/users/farmer/monitoring_cases/case-a/follow_ups/old-{number}"] = {"id": str(number)}
    upload(1, case_id="case-b")
    with pytest.raises(service.FollowUpLimitReached):
        upload(2, case_id="case-b")


def test_each_farmer_and_tree_has_an_independent_limit(store):
    case(store)
    for number in range(3):
        upload(number)
    case(store, farmer="other")
    upload(1, farmer="other")
    case(store, tree="TREE-B", case_id="case-b")
    upload(1, tree="TREE-B", case_id="case-b")
    assert service.follow_up_upload_allowance("other", "TREE-A")["remaining"] == 2
    assert service.follow_up_upload_allowance("farmer", "TREE-B")["remaining"] == 2


def test_concurrent_requests_cannot_exceed_three(store):
    case(store)
    case(store, case_id="case-b")
    def attempt(number):
        try:
            upload(number, case_id="case-a" if number % 2 else "case-b")
            return True
        except service.FollowUpLimitReached:
            return False
    with ThreadPoolExecutor(max_workers=10) as pool:
        assert sum(pool.map(attempt, range(20))) == 3
    assert service.tree_follow_up_count("farmer", "TREE-A") == 3


def test_failed_save_does_not_use_a_chance(store):
    case(store)
    store["/users/farmer/monitoring_cases/case-a/follow_ups/existing"] = {"id": "existing"}
    before = deepcopy(store)
    with pytest.raises(AssertionError):
        upload("existing")
    assert store == before
    assert service.follow_up_upload_allowance("farmer", "TREE-A")["remaining"] == 2
