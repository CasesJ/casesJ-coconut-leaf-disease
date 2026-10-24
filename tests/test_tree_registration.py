"""Identity transaction contract tests; no live Firebase writes."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Lock
from types import SimpleNamespace

import pytest

from app.services import tree_identity_service as identity


@pytest.fixture
def database(monkeypatch):
    docs, lock = {}, Lock()

    class Ref:
        def __init__(self, path=""):
            self.path = path

        def collection(self, name):
            return Ref(f"{self.path}/{name}")

        document = collection

        def get(self, transaction=None):
            data = deepcopy(docs.get(self.path))
            return SimpleNamespace(exists=data is not None, to_dict=lambda: data)

    class Transaction:
        def create(self, ref, data):
            assert ref.path not in docs
            docs[ref.path] = deepcopy(data)

        def update(self, ref, data):
            docs[ref.path].update(deepcopy(data))

        def set(self, ref, data):
            docs[ref.path] = deepcopy(data)

    def transaction(operation):
        with lock:
            before = deepcopy(docs)
            try:
                return operation(Transaction())
            except Exception:
                docs.clear()
                docs.update(before)
                raise

    monkeypatch.setattr(identity.store, "list_cases", lambda uid: [])
    monkeypatch.setattr(identity.store, "_firestore", Ref)
    monkeypatch.setattr(identity, "_transaction", transaction)
    monkeypatch.setattr(identity, "list_trees", lambda uid: [
        data for path, data in docs.items() if path.startswith(f"/users/{uid}/trees/")])
    return docs


def create(docs, uid, rid, selected=None):
    docs.setdefault(f"/users/{uid}/detections/{rid}", {"user_id": uid})
    def care(tx, tree_id):
        ref = identity.store._firestore().collection("care").document(rid)
        tx.set(ref, {"tree_id": tree_id})
        return tree_id
    return identity.commit_care_identity(uid, rid, selected, care)


def test_concurrent_creation_across_accounts(database):
    for i in range(100):
        database[f"/users/farmer-{i % 3}/detections/{i}"] = {"user_id": f"farmer-{i % 3}"}
    with ThreadPoolExecutor(max_workers=12) as pool:
        ids = list(pool.map(lambda i: create(database, f"farmer-{i % 3}", str(i)), range(100)))
    assert len(set(ids)) == 100
    assert all(value.startswith("TREE-") and len(value) == 15 for value in ids)
    for i, tree_id in enumerate(ids):
        assert database[f"/care/{i}"]["tree_id"] == tree_id
        assert database[f"/users/farmer-{i % 3}/detections/{i}"]["tree_id"] == tree_id


def test_existing_tree_reused_and_ids_never_overwritten(database):
    first = create(database, "farmer", "one")
    assert create(database, "farmer", "two", first) == first
    assert create(database, "farmer", "one", first) == first
    with pytest.raises(ValueError):
        create(database, "farmer", "one")
    with pytest.raises(ValueError):
        create(database, "other", "three", first)
    assert database["/users/farmer/detections/one"]["tree_id"] == first


def test_collision_fails_without_committing_care(database, monkeypatch):
    monkeypatch.setattr(identity, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
    create(database, "farmer", "one")
    with pytest.raises(AssertionError):
        create(database, "other", "two")
    assert "/care/two" not in database
    assert "tree_id" not in database["/users/other/detections/two"]


def test_legacy_detection_identity_preserved(database):
    database["/users/farmer/detections/old"] = {"user_id": "farmer", "tree_id": "old-tree"}
    assert create(database, "farmer", "old", "old-tree") == "old-tree"


def test_historical_case_link_is_preserved(database, monkeypatch):
    monkeypatch.setattr(identity.store, "list_cases", lambda uid: [
        {"tree_id": "legacy-tree", "baseline_detection_id": "old"}])
    with pytest.raises(ValueError):
        create(database, "farmer", "old")
    assert create(database, "farmer", "old", "legacy-tree") == "legacy-tree"


def test_simultaneous_registration_of_one_detection_does_not_replace_identity(database):
    database["/users/farmer/detections/one"] = {"user_id": "farmer"}
    def attempt(_):
        try:
            return create(database, "farmer", "one")
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(attempt, range(20)))
    successful = [value for value in ids if value]
    assert len(successful) == 1
    assert database["/users/farmer/detections/one"]["tree_id"] == successful[0]


def test_tree_listing_preserves_farmer_expert_authorization(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.core.security import verify_verified_firebase_token
    monkeypatch.setattr(identity, "list_trees", lambda uid: [{"id": "tree", "farmer_id": uid}])
    try:
        app.dependency_overrides[verify_verified_firebase_token] = lambda: {"uid": "farmer"}
        client = TestClient(app)
        assert client.get("/monitoring/trees").json()["trees"][0]["farmer_id"] == "farmer"
        assert client.get("/expert/monitoring/trees/other").status_code == 403
        app.dependency_overrides[verify_verified_firebase_token] = lambda: {"uid": "expert", "role": "expert"}
        assert client.get("/monitoring/trees").status_code == 403
        assert client.get("/expert/monitoring/trees/farmer").status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_audit_enrichment_uses_exact_owner_and_record_link(monkeypatch):
    from app.api import expert
    monkeypatch.setattr(identity.store, "list_expert_cases", lambda: [
        {"farmer_id": "farmer", "baseline_detection_id": "one", "monitoring_status": "finish"}])
    events = [{"target": {"user_id": "farmer", "record_id": "one"}},
              {"target": {"user_id": "other", "record_id": "one"}},
              {"target": {}, "tree_id": "existing"}]
    monkeypatch.setattr(expert, "_require_expert", lambda _: None)
    monkeypatch.setattr(expert, "read_expert_audit_events", lambda **_: deepcopy(events))
    monkeypatch.setattr(expert, "get_all_user_records", lambda **_: [
        {"user_id": "farmer", "id": "one", "tree_id": "TREE-ABC"}])
    monkeypatch.setattr(expert, "present_detection_record", lambda *args, **kwargs: {})
    result = expert.get_expert_audit_log(decoded={})["events"]
    assert result[0]["monitoring_status"] == "finish"
    assert result[1]["monitoring_status"] == "waiting"
    assert result[0]["tree_id"] == "TREE-ABC"
    assert not result[1].get("tree_id")
    assert result[2]["tree_id"] == "existing"
    assert "tree_id" not in events[0]
