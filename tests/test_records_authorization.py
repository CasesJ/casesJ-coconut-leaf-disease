"""Offline authorization tests for detection record retrieval routes."""

from fastapi.testclient import TestClient
import pytest

from app.api import expert as expert_api
from app.api import records as records_api
from app.core import security
from app.main import app
from app.services import detection_service


@pytest.fixture
def client():
    app.dependency_overrides.clear()
    yield TestClient(app)
    app.dependency_overrides.clear()


def _set_token_claims(monkeypatch, claims_by_token):
    def verify(token):
        if token not in claims_by_token:
            raise ValueError("invalid test token")
        value = claims_by_token[token]
        if isinstance(value, Exception):
            raise value
        return dict(value)

    monkeypatch.setattr(security, "verify_token", verify)


def _auth(token="farmer-token"):
    return {"Authorization": f"Bearer {token}"}


def test_records_reject_missing_authentication(client):
    response = client.get("/records")
    assert response.status_code in {401, 403}


def test_farmer_reads_records_bound_to_verified_uid(client, monkeypatch):
    _set_token_claims(monkeypatch, {"farmer-token": {"uid": "farmer-a"}})
    fetched_uids = []
    record = {"id": "record-a", "user_id": "farmer-a", "detections": []}
    monkeypatch.setattr(
        records_api,
        "get_user_records",
        lambda uid: fetched_uids.append(uid) or [dict(record)],
    )
    response = client.get("/records", headers=_auth())

    assert response.status_code == 200
    assert [item["user_id"] for item in response.json()] == ["farmer-a"]
    assert fetched_uids == ["farmer-a"]


def test_user_id_query_cannot_select_another_farmers_records(client, monkeypatch):
    _set_token_claims(monkeypatch, {"farmer-token": {"uid": "farmer-a"}})
    fetched_uids = []
    monkeypatch.setattr(
        records_api,
        "get_user_records",
        lambda uid: fetched_uids.append(uid) or [
            {"id": "record-a", "user_id": uid, "detections": []}
        ],
    )

    response = client.get("/records?user_id=farmer-b", headers=_auth())

    assert response.status_code == 200
    assert [item["user_id"] for item in response.json()] == ["farmer-a"]
    assert fetched_uids == ["farmer-a"]


def test_experts_are_blocked_from_farmer_route_and_use_expert_workflow(client, monkeypatch):
    _set_token_claims(monkeypatch, {
        "farmer-token": {"uid": "farmer-a"},
        "expert-token": {"uid": "expert-1", "role": "expert"},
        "unverified-expert-token": {
            "uid": "unverified-expert", "email": "expert@example.test",
            "email_verified": False,
        },
    })
    records = [{"id": "record-a", "user_id": "farmer-a", "detections": []}]
    monkeypatch.setattr(expert_api, "get_all_user_records", lambda limit: records)
    monkeypatch.setattr(
        expert_api,
        "present_detection_record",
        lambda record, *, expert=False: {**record, "expert_view": expert},
    )

    assert client.get("/records", headers=_auth("expert-token")).status_code == 403
    assert client.get("/expert/records", headers=_auth()).status_code == 403
    assert client.get("/expert/records", headers=_auth("unverified-expert-token")).status_code == 403

    response = client.get("/expert/records?status=all", headers=_auth("expert-token"))
    assert response.status_code == 200
    assert response.json()["records"] == [{
        "id": "record-a", "user_id": "farmer-a", "detections": [], "expert_view": True,
    }]


@pytest.mark.parametrize("error_name", ["InvalidIdTokenError", "ExpiredIdTokenError"])
def test_invalid_or_expired_firebase_tokens_are_rejected(client, monkeypatch, error_name):
    token_error = type(error_name, (Exception,), {})("test token rejected")
    _set_token_claims(monkeypatch, {"bad-token": token_error})

    response = client.get("/records", headers=_auth("bad-token"))

    assert response.status_code == 401


def test_unsigned_offline_fallback_identity_is_rejected(client, monkeypatch):
    _set_token_claims(monkeypatch, {
        "offline-token": {"uid": "farmer-a", "token_verified_offline": True},
    })

    response = client.get("/records", headers=_auth("offline-token"))

    assert response.status_code == 401


def test_legacy_cross_farmer_csv_export_requires_expert_authorization(client, monkeypatch):
    _set_token_claims(monkeypatch, {
        "farmer-token": {"uid": "farmer-a"},
        "expert-token": {"uid": "expert-1", "role": "expert"},
    })
    monkeypatch.setattr(records_api, "generate_detections_csv", lambda: "Disease Name\n")

    assert client.get("/api/export-csv", headers=_auth()).status_code == 403
    response = client.get("/api/export-csv", headers=_auth("expert-token"))
    assert response.status_code == 200
    assert response.text == "Disease Name\n"


def test_user_record_list_uses_verified_auth_dependency(client, monkeypatch):
    _set_token_claims(monkeypatch, {"farmer-token": {"uid": "farmer-a"}})
    fetched_uids = []
    monkeypatch.setattr(
        detection_service,
        "get_user_records",
        lambda uid, limit=None: fetched_uids.append((uid, limit)) or [],
    )

    response = client.get("/detections/my-records?limit=25", headers=_auth())

    assert response.status_code == 200
    assert fetched_uids == [("farmer-a", 25)]
