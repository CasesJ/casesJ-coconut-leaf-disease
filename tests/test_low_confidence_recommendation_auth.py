"""Offline authorization tests for farmer recommendation writes."""

from fastapi.testclient import TestClient
import pytest

from app.api import detections as detections_api
from app.core import security
from app.main import app


@pytest.fixture
def client():
    app.dependency_overrides.clear()
    yield TestClient(app)
    app.dependency_overrides.clear()


def _set_token_result(monkeypatch, result):
    def verify(token):
        if isinstance(result, Exception):
            raise result
        return dict(result)

    monkeypatch.setattr(security, "verify_token", verify)


def _headers(token="farmer-token"):
    return {"Authorization": f"Bearer {token}"}


def _payload():
    return {
        "fertilizer": "Balanced palm fertilizer",
        "treatment": "Inspect affected fronds and follow expert guidance",
        "prevention": ["Improve drainage", "Inspect weekly"],
        "note": "Farmer review",
    }


def test_verified_farmer_can_save_review_for_their_low_confidence_record(client, monkeypatch):
    _set_token_result(monkeypatch, {"uid": "farmer-a", "email": "farmer@example.test"})
    lookup_calls = []
    update_calls = []
    monkeypatch.setattr(
        detections_api,
        "find_record_for_user",
        lambda uid, record_id: lookup_calls.append((uid, record_id)) or {
            "id": record_id,
            "user_id": "farmer-a",
            "detections": [{"class": "Bud Rot", "confidence": 0.42}],
        },
    )
    monkeypatch.setattr(
        detections_api,
        "update_record_everywhere",
        lambda uid, record_id, updates: update_calls.append((uid, record_id, updates)) or True,
    )

    response = client.put(
        "/detections/my-records/record-a/low-confidence-recommendation",
        headers=_headers(),
        json=_payload(),
    )

    assert response.status_code == 200
    assert lookup_calls == [("farmer-a", "record-a")]
    assert len(update_calls) == 1
    uid, record_id, updates = update_calls[0]
    assert (uid, record_id) == ("farmer-a", "record-a")
    assert updates["recommendation_snapshot"]["disease"] == "Bud Rot"
    assert updates["recommendation_snapshot"]["confidence_percent"] == 42.0
    assert updates["verification_status"] == "pending_verification"
    assert updates["verified_by"] is None


def test_write_rejects_missing_authentication_without_lookup(client, monkeypatch):
    lookup_calls = []
    monkeypatch.setattr(
        detections_api,
        "find_record_for_user",
        lambda *args: lookup_calls.append(args),
    )

    response = client.put(
        "/detections/my-records/record-a/low-confidence-recommendation",
        json=_payload(),
    )

    assert response.status_code in {401, 403}
    assert lookup_calls == []


def test_write_rejects_expired_firebase_token_without_lookup(client, monkeypatch):
    expired_error = type("ExpiredIdTokenError", (Exception,), {})("expired test token")
    _set_token_result(monkeypatch, expired_error)
    lookup_calls = []
    monkeypatch.setattr(
        detections_api,
        "find_record_for_user",
        lambda *args: lookup_calls.append(args),
    )

    response = client.put(
        "/detections/my-records/record-a/low-confidence-recommendation",
        headers=_headers("expired-token"),
        json=_payload(),
    )

    assert response.status_code == 401
    assert lookup_calls == []


def test_write_rejects_unsigned_fallback_identity(client, monkeypatch):
    _set_token_result(monkeypatch, {
        "uid": "farmer-a",
        "token_verified_offline": True,
    })
    lookup_calls = []
    monkeypatch.setattr(
        detections_api,
        "find_record_for_user",
        lambda *args: lookup_calls.append(args),
    )

    response = client.put(
        "/detections/my-records/record-a/low-confidence-recommendation",
        headers=_headers("unsigned-token"),
        json=_payload(),
    )

    assert response.status_code == 401
    assert lookup_calls == []


def test_farmer_cannot_write_another_farmers_record(client, monkeypatch):
    _set_token_result(monkeypatch, {"uid": "farmer-a"})
    lookup_calls = []
    update_calls = []
    monkeypatch.setattr(
        detections_api,
        "find_record_for_user",
        lambda uid, record_id: lookup_calls.append((uid, record_id)) or {
            "id": record_id,
            "user_id": "farmer-b",
            "detections": [{"class": "Bud Rot", "confidence": 0.42}],
        },
    )
    monkeypatch.setattr(
        detections_api,
        "update_record_everywhere",
        lambda *args: update_calls.append(args) or True,
    )

    response = client.put(
        "/detections/my-records/farmer-b-record/low-confidence-recommendation",
        headers=_headers(),
        json=_payload(),
    )

    assert response.status_code == 404
    assert lookup_calls == [("farmer-a", "farmer-b-record")]
    assert update_calls == []
