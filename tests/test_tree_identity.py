"""Identity workflow regression tests using only in-memory Firestore and media."""
import base64
from copy import deepcopy
from io import BytesIO
import json
import math
import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient
from PIL import Image
import piexif
import pytest

from app.api import monitoring, tree_identity as api
from app.core.security import verify_firebase_token
from app.domain.tree_identity import compare_gps, normalize_gps
from app.infrastructure.firebase import cloud_storage
from app.infrastructure.imaging import tree_reference as images
from app.main import app
from app.services import monitoring_service as store, tree_identity_service as identity

CASE_PATH = "users/farmer-a/monitoring_cases/case-1"
FOLLOW_PATH = CASE_PATH + "/follow_ups/old-follow-up"
EXPERT_BASE = "/expert/monitoring/cases/farmer-a/case-1"
FARMER_BASE = "/monitoring/cases/case-1"


class MemoryDB:
    """Small atomic Firestore double, including optimistic transaction retries."""
    def __init__(self):
        self.docs = {}
        self.before_commit = None

    def collection(self, name):
        return Ref(self, name)

    def collection_group(self, name):
        return SimpleNamespace(stream=lambda: [Ref(self, path).get() for path in self.docs
            if path.split("/")[-2] == name])

    def batch(self):
        return Writes(self)

    def transact(self, operation):
        for _ in range(3):
            tx = Writes(self)
            result = operation(tx)
            if self.before_commit:
                hook, self.before_commit = self.before_commit, None
                hook()
            if any(self.docs.get(key) != value for key, value in tx.reads.items()):
                continue
            tx.commit()
            return result
        raise RuntimeError("Concurrent modification")


class Ref:
    def __init__(self, db, path):
        self.db, self.path = db, path

    def collection(self, name):
        return Ref(self.db, self.path + "/" + name)

    document = collection

    def get(self, transaction=None):
        value = deepcopy(self.db.docs.get(self.path))
        if transaction:
            transaction.reads[self.path] = value
        return SimpleNamespace(exists=value is not None, to_dict=lambda: deepcopy(value))

    def order_by(self, _field):
        return self

    def stream(self):
        prefix = self.path + "/"
        return [Ref(self.db, path).get() for path in sorted(self.db.docs)
                if path.startswith(prefix) and "/" not in path[len(prefix):]]


class Writes:
    def __init__(self, db):
        self.db, self.actions, self.reads = db, [], {}

    def create(self, ref, data):
        self.actions.append(("create", ref.path, deepcopy(data)))

    def update(self, ref, data):
        self.actions.append(("update", ref.path, deepcopy(data)))

    def set(self, ref, data):
        self.actions.append(("set", ref.path, deepcopy(data)))

    def commit(self):
        proposed = deepcopy(self.db.docs)
        for action, path, data in self.actions:
            if action == "create":
                assert path not in proposed
            if action == "update":
                assert path in proposed
                proposed[path].update(data)
            else:
                proposed[path] = data
        self.db.docs = proposed


def photo(fmt="JPEG", gps=False, size=(24, 40)):
    stream = BytesIO()
    options = {}
    if gps:
        options["exif"] = piexif.dump({"GPS": {
            piexif.GPSIFD.GPSLatitudeRef: b"N", piexif.GPSIFD.GPSLatitude: ((7, 1), (0, 1), (0, 1)),
            piexif.GPSIFD.GPSLongitudeRef: b"E", piexif.GPSIFD.GPSLongitude: ((125, 1), (0, 1), (0, 1)),
            piexif.GPSIFD.GPSDOP: (99, 1), piexif.GPSIFD.GPSHPositioningError: (3, 1)}})
    Image.new("RGB", size, "green").save(stream, fmt, **options)
    return stream.getvalue()


@pytest.fixture
def env(monkeypatch):
    db = MemoryDB()
    case = {"id": "case-1", "farmer_id": "farmer-a", "tree_id": "expert-tree-12",
            "baseline_detection_id": "detection-1", "disease": "Bud Rot", "treatment_record_id": "treatment-1",
            "next_follow_up_date": "2030-01-01", "latest_review_status": "expert_confirmed",
            "latest_confirmed_outcome": "improving", "latest_follow_up_id": "old-follow-up",
            "baseline_image_reference": {"image_url": "https://storage.test/unchanged-disease.jpg"},
            "baseline_gps_snapshot": {"latitude": 7, "longitude": 125, "source": "exif"}}
    db.docs[CASE_PATH] = case
    db.docs[FOLLOW_PATH] = {"id": "old-follow-up", "farmer_id": "farmer-a", "case_id": "case-1",
                          "tree_id": "expert-tree-12", "created_at": "2026-10-01",
                          "review_status": "expert_confirmed", "confirmed_outcome": "improving",
                          "expert_note": "Historical assessment", "predictions": [{"class": "Bud Rot", "confidence": 0.8}]}
    db.docs[CASE_PATH + "/treatment_applications/app-1"] = {"id": "app-1", "case_id": "case-1",
        "farmer_id": "farmer-a", "treatment_performed": "Pruned affected fronds", "application_date": "2026-09-30"}
    db.docs["users/farmer-a/treatment_records/treatment-1"] = {"recommendation_snapshot": {"recommendations": {"treatment": "Historical plan"}}}
    originals = deepcopy(db.docs)
    monkeypatch.setattr(store, "_firestore", lambda: db)
    monkeypatch.setattr(identity, "_transaction", db.transact)
    actor = {"uid": "expert-1", "role": "expert"}
    app.dependency_overrides[verify_firebase_token] = lambda: dict(actor)
    assets, deleted, inference_calls = {}, [], []

    def upload(reference, content):
        assets[reference["id"]] = content
        return {**reference, "storage_path": cloud_storage.tree_reference_path(reference), "storage_generation": "1"}

    def delete(reference):
        deleted.append(reference["id"])
        assets.pop(reference["id"], None)

    async def allowed(*args):
        pass

    monkeypatch.setattr(api, "upload_private_tree_reference", upload)
    monkeypatch.setattr(monitoring, "upload_private_tree_reference", upload)
    monkeypatch.setattr(api, "delete_new_tree_reference", delete)
    monkeypatch.setattr(monitoring, "delete_new_tree_reference", delete)
    monkeypatch.setattr(api, "read_private_tree_reference", lambda ref: assets[ref["id"]])
    monkeypatch.setattr(monitoring, "upload_monitoring_evidence", lambda *args: "https://storage.test/disease.jpg")
    monkeypatch.setattr(monitoring, "delete_monitoring_evidence", lambda *args: deleted.append(args[3]))
    monkeypatch.setattr(api.limiter, "rolling", allowed)
    monkeypatch.setattr(api.limiter, "image_quota_24h", allowed)

    def infer(image, conf):
        inference_calls.append(image.copy())
        return {"image": image, "detections": [{"class": "Bud Rot", "confidence": 0.81}]}

    monkeypatch.setitem(sys.modules, "model", SimpleNamespace(detector=SimpleNamespace(predict=infer)))
    return SimpleNamespace(db=db, client=TestClient(app), actor=actor, assets=assets,
                           deleted=deleted, originals=originals, inference_calls=inference_calls)


def baseline(env, **kwargs):
    return env.client.post(EXPERT_BASE + "/tree-reference", files={"file": ("tree.jpg", photo(**kwargs), "image/jpeg")})


def follow_up(env, tree=True, gps=False, **data):
    env.actor.clear()
    env.actor.update(uid="farmer-a")
    files = {"file": ("disease.jpg", photo(size=(20, 20)), "image/jpeg")}
    if tree:
        files["tree_reference_file"] = ("whole-tree.jpg", photo(gps=gps), "image/jpeg")
    return env.client.post(FARMER_BASE + "/follow-ups", files=files, data=data)


def review(env, record, status="confirmed", **extra):
    env.actor.update(uid="expert-1", role="expert")
    ref_id = (env.db.docs[CASE_PATH].get("tree_reference") or {}).get("id")
    return env.client.put(EXPERT_BASE + f"/follow-ups/{record['id']}/identity-review", json={
        "status": status, "baseline_reference_id": ref_id, **extra})


def test_complete_workflow_preserves_history_and_separates_outcomes(env):
    response = baseline(env, gps=True)
    assert response.status_code == 201, response.text
    tree_ref = response.json()
    assert tree_ref["gps_data"]["accuracy_m"] == 3
    assert "image_url" not in tree_ref and "token" not in json.dumps(tree_ref)
    assert env.db.docs[CASE_PATH] == {**env.originals[CASE_PATH], "tree_reference": tree_ref}
    upload = follow_up(env, gps=True, tree_id="attacker-tree", farmer_id="someone-else", identity_review="confirmed")
    assert upload.status_code == 201, upload.text
    record = upload.json()
    assert record["tree_id"] == "expert-tree-12" and record["farmer_id"] == "farmer-a"
    assert record["tree_reference"]["follow_up_id"] == record["id"]
    assert record["tree_reference"]["case_id"] == "case-1"
    assert record["identity_review"] is None
    assert record["identity_baseline_reference_id"] == tree_ref["id"]
    assert len(env.inference_calls) == 1 and env.inference_calls[0].shape[:2] == (20, 20)
    before = deepcopy(env.db.docs)
    reviewed = review(env, record, explanation="Same trunk scar and shed")
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "confirmed"
    assert reviewed.json()["gps_evidence"]["status"] == "within_tolerance"
    path = CASE_PATH + "/follow_ups/" + record["id"]
    assert env.db.docs[path] == {**before[path], "identity_review": reviewed.json()}
    assert env.db.docs[CASE_PATH] == before[CASE_PATH]
    for key, original in env.originals.items():
        if key != CASE_PATH:
            assert env.db.docs[key] == original
    env.actor.clear()
    env.actor.update(uid="farmer-a")
    overview = env.client.get(FARMER_BASE + "/overview")
    assert overview.status_code == 200
    assert overview.json()["treatment_applications"][0]["treatment_performed"] == "Pruned affected fronds"
    assert env.client.get(FARMER_BASE + f"/tree-references/{tree_ref['id']}").content == photo(gps=True)
    media = env.client.get(FARMER_BASE + f"/tree-references/{record['tree_reference']['id']}?follow_up_id={record['id']}")
    assert media.status_code == 200
    assert media.headers["cache-control"] == "private, no-store"
    history = env.client.get(FARMER_BASE + f"/follow-ups/{record['id']}/identity-reviews")
    assert len(history.json()["identity_reviews"]) == 1


def test_legacy_case_is_readable_without_writes_or_inferred_identity(env):
    original = deepcopy(env.db.docs)
    response = env.client.get(EXPERT_BASE + "/overview")
    assert response.status_code == 200
    assert response.json()["follow_ups"][0]["identity_review"] is None
    assert env.db.docs == original
    assert review(env, env.db.docs[FOLLOW_PATH]).status_code == 422
    assert review(env, env.db.docs[FOLLOW_PATH], status="uncertain").status_code == 200
    assert env.db.docs[FOLLOW_PATH]["confirmed_outcome"] == "improving"


def test_no_gps_and_late_baseline_do_not_prevent_manual_review(env):
    record = follow_up(env).json()
    assert review(env, record).status_code == 422
    assert baseline(env).status_code == 201
    response = review(env, record)
    assert response.status_code == 200
    assert response.json()["gps_evidence"]["status"] == "gps_unavailable"


def test_reference_cannot_be_replaced(env):
    first = baseline(env).json()
    original = deepcopy(env.db.docs)
    assert baseline(env).status_code == 409
    assert env.db.docs == original
    assert list(env.assets) == [first["id"]]


def test_review_history_revision_conflicts_and_outcome_independence(env):
    baseline(env)
    record = follow_up(env).json()
    first = review(env, record, status="uncertain").json()
    assert review(env, record, status="mismatch").status_code == 409
    second = review(env, record, status="mismatch", expected_revision=first["revision"]).json()
    assert second["revision"] == 2
    response = env.client.put(EXPERT_BASE + f"/follow-ups/{record['id']}/review",
        json={"confirmed_outcome": "improving", "note": "Outcome reviewed independently"})
    assert response.status_code == 200
    stored = env.db.docs[CASE_PATH + "/follow_ups/" + record["id"]]
    assert stored["identity_review"] == second
    history = env.client.get(EXPERT_BASE + f"/follow-ups/{record['id']}/identity-reviews").json()["identity_reviews"]
    assert [item["status"] for item in history] == ["uncertain", "mismatch"]


def test_concurrent_baseline_upload_retains_winner_and_cleans_only_loser(env):
    winner = images.reference_metadata(env.db.docs[CASE_PATH], "winner", "expert-2", photo(), "image/jpeg", "tree.jpg")
    env.db.before_commit = lambda: env.db.docs[CASE_PATH].update(tree_reference=winner)
    response = baseline(env)
    assert response.status_code == 409
    assert env.db.docs[CASE_PATH]["tree_reference"] == winner
    assert len(env.deleted) == 1 and "winner" not in env.deleted


def test_concurrent_review_detects_new_revision(env):
    baseline(env)
    record = follow_up(env).json()
    path = CASE_PATH + "/follow_ups/" + record["id"]
    env.db.before_commit = lambda: env.db.docs[path].update(identity_review={"revision": 1, "status": "uncertain"})
    assert review(env, record).status_code == 409
    assert env.db.docs[path]["identity_review"]["status"] == "uncertain"


@pytest.mark.parametrize("actor", [{"uid": "farmer-b"}, {"uid": "farmer-a"}, {"uid": "expert-x", "email": "expert@example.test", "email_verified": False}])
def test_non_experts_cannot_register_or_review(env, actor):
    env.actor.clear()
    env.actor.update(actor)
    assert baseline(env).status_code == 403
    assert env.client.put(EXPERT_BASE + "/follow-ups/old-follow-up/identity-review", json={"status": "uncertain"}).status_code == 403
    assert env.assets == {} and env.db.docs == env.originals


def test_other_farmer_cannot_read_reference_or_history(env):
    reference = baseline(env).json()
    env.actor.clear()
    env.actor.update(uid="farmer-b")
    assert env.client.get(FARMER_BASE + f"/tree-references/{reference['id']}").status_code == 404
    assert env.client.get(FARMER_BASE + "/follow-ups/old-follow-up/identity-reviews").status_code == 404
    assert env.client.get(EXPERT_BASE + f"/tree-references/{reference['id']}").status_code == 403


def test_reference_ids_cannot_be_substituted_across_follow_ups(env):
    ref = baseline(env).json()
    record = follow_up(env).json()
    assert env.client.get(FARMER_BASE + f"/tree-references/{ref['id']}?follow_up_id={record['id']}").status_code == 404
    assert env.client.get(FARMER_BASE + f"/tree-references/{record['tree_reference']['id']}").status_code == 404
    env.actor.update(role="expert")
    assert env.client.put(EXPERT_BASE + f"/follow-ups/{record['id']}/identity-review", json={"status": "confirmed", "baseline_reference_id": "wrong"}).status_code == 409


@pytest.mark.parametrize("extra", [{"status": "auto_confirmed"}, {"status": "confirmed", "tree_id": "fake"},
                                  {"status": "uncertain", "reviewed_by_expert_uid": "forged"},
                                  {"status": "uncertain", "explanation": "x" * 2001}])
def test_review_payload_validation(env, extra):
    assert env.client.put(EXPERT_BASE + "/follow-ups/old-follow-up/identity-review", json=extra).status_code == 422


@pytest.mark.parametrize("payload,mime,status", [(b"", "image/jpeg", 400), (b"<svg/>", "image/jpeg", 400),
    (photo(), "image/png", 400), (photo("GIF"), "image/gif", 400)])
def test_bad_reference_uploads_never_infer_or_persist(env, payload, mime, status):
    response = env.client.post(EXPERT_BASE + "/tree-reference", files={"file": ("tree.jpg", payload, mime)})
    assert response.status_code == status
    env.actor.clear()
    env.actor.update(uid="farmer-a")
    response = env.client.post(FARMER_BASE + "/follow-ups", files={
        "file": ("disease.jpg", photo(), "image/jpeg"), "tree_reference_file": ("tree.jpg", payload, mime)})
    assert response.status_code == status
    assert not env.inference_calls and not env.assets and env.db.docs == env.originals


def test_reference_size_limit(env, monkeypatch):
    monkeypatch.setattr(images, "MAX_IMAGE_UPLOAD_BYTES", 20)
    assert baseline(env).status_code == 413
    assert not env.assets


@pytest.mark.parametrize("committed,read_fails", [(False, False), (True, False), (True, True), (False, True)])
def test_ambiguous_baseline_commit_never_deletes_possible_evidence(env, monkeypatch, committed, read_fails):
    get_case = store.get_case

    def fail(farmer, case, ref):
        if committed:
            env.db.docs[CASE_PATH]["tree_reference"] = ref
        if read_fails:
            monkeypatch.setattr(store, "get_case", lambda *args: (_ for _ in ()).throw(RuntimeError("unavailable")))
        raise RuntimeError("Commit response lost")

    monkeypatch.setattr(identity, "register_baseline", fail)
    assert baseline(env).status_code == 503
    assert bool(env.deleted) == (not committed and not read_fails)
    monkeypatch.setattr(store, "get_case", get_case)


@pytest.mark.parametrize("committed,read_fails", [(False, False), (True, False), (True, True), (False, True)])
def test_ambiguous_follow_up_commit_preserves_referenced_photos(env, monkeypatch, committed, read_fails):
    def fail(farmer, case, record):
        if committed:
            env.db.docs[CASE_PATH + "/follow_ups/" + record["id"]] = record
        if read_fails:
            monkeypatch.setattr(store, "get_follow_up", lambda *args: (_ for _ in ()).throw(RuntimeError("unavailable")))
        raise RuntimeError("Commit response lost")

    monkeypatch.setattr(store, "save_follow_up", fail)
    assert follow_up(env).status_code == 503
    assert bool(env.deleted) == (not committed and not read_fails)
    assert env.db.docs[FOLLOW_PATH] == env.originals[FOLLOW_PATH]


def test_missing_invalid_expired_and_unsigned_fallback_tokens_are_rejected(env, monkeypatch):
    import firebase_config
    app.dependency_overrides.clear()
    url = EXPERT_BASE + "/follow-ups/old-follow-up/identity-review"
    assert env.client.put(url, json={"status": "uncertain"}).status_code in {401, 403}
    monkeypatch.setattr(firebase_config, "ALLOW_UNVERIFIED_FIREBASE_TOKENS", True)
    monkeypatch.setattr(firebase_config.firebase_auth, "verify_id_token", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad signature")))
    def jwt(payload):
        encode = lambda obj: base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
        return encode({"alg": "none"}) + "." + encode(payload) + ".fake"
    for token in ("invalid", jwt({"uid": "expert-1", "role": "expert", "exp": 1}),
                  jwt({"uid": "expert-1", "role": "expert", "exp": 4_000_000_000, "token_verified_offline": False})):
        headers = {"Authorization": "Bearer " + token}
        assert env.client.put(url, json={"status": "uncertain"}, headers=headers).status_code == 401
        assert env.client.get(FARMER_BASE + "/tree-references/fake", headers=headers).status_code == 401
        assert env.client.post(EXPERT_BASE + "/tree-reference", files={"file": ("tree.jpg", photo(), "image/jpeg")}, headers=headers).status_code == 401
        assert env.client.post(FARMER_BASE + "/follow-ups", files={"file": ("disease.jpg", photo(), "image/jpeg")}, headers=headers).status_code == 401
    assert env.db.docs == env.originals


@pytest.mark.parametrize("value", [None, {}, {"latitude": 91, "longitude": 0}, {"latitude": 0, "longitude": -181},
    {"latitude": math.nan, "longitude": 0}, {"latitude": math.inf, "longitude": 0},
    {"latitude": True, "longitude": 0}, {"latitude": "bad", "longitude": 0}, "not-json"])
def test_missing_or_invalid_gps_is_unavailable(value):
    assert compare_gps(value, {"latitude": 0, "longitude": 0})["status"] == "gps_unavailable"


def test_gps_zero_coordinates_boundary_antimeridian_and_dop():
    origin = {"latitude": 0, "longitude": 0, "accuracy": 999, "source": "exif"}
    assert normalize_gps(origin)["accuracy_m"] is None
    assert compare_gps(origin, origin)["status"] == "within_tolerance"
    lat = math.degrees(25 / 6_371_008.8)
    assert compare_gps(origin, {"latitude": lat * .999, "longitude": 0})["status"] == "within_tolerance"
    assert compare_gps(origin, {"latitude": lat * 1.001, "longitude": 0})["status"] == "outside_tolerance"
    uncertain = compare_gps({**origin, "accuracy_m": 10}, {"latitude": lat, "longitude": 0, "accuracy_m": 10})
    assert uncertain["status"] == "inconclusive_accuracy"
    assert compare_gps({"latitude": 0, "longitude": 179.99999}, {"latitude": 0, "longitude": -179.99999})["distance_m"] < 3
    assert "identity_review" not in uncertain and "confirmed" not in uncertain.values()


def test_private_storage_has_no_download_token_and_prevents_overwrites(monkeypatch):
    calls = []
    blob = SimpleNamespace(generation=7, upload_from_string=lambda content, **kwargs: calls.append((content, kwargs)))
    bucket = SimpleNamespace(blob=lambda path: blob)
    from firebase_admin import storage
    monkeypatch.setattr(storage, "bucket", lambda: bucket)
    ref = {"farmer_id": "farmer-a", "case_id": "case-1", "id": "reference-1", "content_type": "image/jpeg", "sha256": "test-hash"}
    saved = cloud_storage.upload_private_tree_reference(ref, b"test")
    assert calls == [(b"test", {"content_type": "image/jpeg", "if_generation_match": 0})]
    assert "firebaseStorageDownloadTokens" not in blob.metadata
    assert blob.cache_control == "private, no-store"
    assert saved["storage_generation"] == "7"
    with pytest.raises(ValueError):
        cloud_storage.tree_reference_path({**ref, "id": "../historical"})


def test_partial_reference_storage_failure_keeps_history(env, monkeypatch):
    def unavailable(*args):
        raise RuntimeError("Private storage unavailable")
    monkeypatch.setattr(monitoring, "upload_private_tree_reference", unavailable)
    assert follow_up(env).status_code == 503
    assert env.deleted == ["annotated", "original"]
    assert env.db.docs == env.originals


def test_decoding_and_pixel_limits(env):
    import struct
    import zlib
    # A real PNG header advertising >40 MP, rejected before pixel allocation.
    data = bytearray(photo("PNG"))
    data[16:24] = struct.pack(">II", 8000, 6000)
    data[29:33] = struct.pack(">I", zlib.crc32(data[12:29]))
    for content, mime in ((bytes(data), "image/png"), (photo()[:100], "image/jpeg")):
        result = env.client.post(EXPERT_BASE + "/tree-reference", files={"file": ("bad-image", content, mime)})
        assert result.status_code == 400
    assert not env.assets and not env.inference_calls


def test_far_gps_never_sets_identity_or_changes_case(env, monkeypatch):
    baseline(env, gps=True)
    env.db.docs[CASE_PATH]["tree_reference"]["gps_data"] = {"latitude": 0, "longitude": 0, "source": "exif"}
    record = follow_up(env, gps=True).json()
    overview = env.client.get(FARMER_BASE + "/overview").json()
    current = next(item for item in overview["follow_ups"] if item["id"] == record["id"])
    assert current["identity_gps_evidence"]["status"] == "outside_tolerance"
    assert current["identity_review"] is None
    assert current["review_status"] == "pending_expert_review"


def test_mismatched_stored_tree_and_forged_reference_are_rejected(env):
    ref = baseline(env).json()
    record = follow_up(env).json()
    path = CASE_PATH + "/follow_ups/" + record["id"]
    env.db.docs[path]["tree_reference"]["farmer_id"] = "farmer-b"
    assert env.client.get(FARMER_BASE + f"/tree-references/{record['tree_reference']['id']}?follow_up_id={record['id']}").status_code == 404
    assert review(env, record).status_code == 422
    env.db.docs[path]["tree_id"] = "another-tree"
    assert review(env, record).status_code == 404


def test_positive_verified_authentication_is_used(env, monkeypatch):
    import firebase_config
    app.dependency_overrides.clear()
    monkeypatch.setattr(firebase_config.firebase_auth, "verify_id_token", lambda token, **kwargs:
                        {"uid": "expert-1", "role": "expert"} if token == "verified-test-token" else {})
    response = env.client.post(EXPERT_BASE + "/tree-reference", headers={"Authorization": "Bearer verified-test-token"},
                               files={"file": ("tree.jpg", photo(), "image/jpeg")})
    assert response.status_code == 201
    assert response.json()["uploaded_by_uid"] == "expert-1"
