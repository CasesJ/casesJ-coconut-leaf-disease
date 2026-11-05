"""Offline auth and ownership tests for notification and identity routes."""

from fastapi.testclient import TestClient
import pytest

import firebase_config
from app.api import auth as auth_api
from app.api import notifications as notifications_api
from app.core import security
from app.main import app


@pytest.fixture
def client():
    app.dependency_overrides.clear()
    yield TestClient(app)
    app.dependency_overrides.clear()


def _set_bearer_result(monkeypatch, result):
    def verify(token):
        if isinstance(result, Exception):
            raise result
        return dict(result)

    monkeypatch.setattr(security, "verify_token", verify)


def _set_body_token_result(monkeypatch, result):
    def verify(token):
        if isinstance(result, Exception):
            raise result
        return dict(result)

    monkeypatch.setattr(auth_api, "verify_token", verify)


def _headers(token="valid-token"):
    return {"Authorization": f"Bearer {token}"}


def test_verified_farmer_can_read_update_and_clear_only_their_notifications(client, monkeypatch):
    _set_bearer_result(monkeypatch, {"uid": "farmer-a", "email": "a@example.test"})
    read_uids = []
    marked_uids = []
    cleared_uids = []
    notices = [{"id": "notice-a", "read": False}]
    monkeypatch.setattr(
        notifications_api,
        "get_user_notifications",
        lambda uid: read_uids.append(uid) or notices,
    )
    monkeypatch.setattr(
        notifications_api,
        "mark_notifications_read",
        lambda uid: marked_uids.append(uid),
    )
    monkeypatch.setattr(
        notifications_api,
        "clear_notifications",
        lambda uid: cleared_uids.append(uid),
    )

    read_response = client.get("/notifications?user_id=farmer-b", headers=_headers())
    mark_response = client.post("/notifications/mark-read?user_id=farmer-b", headers=_headers())
    clear_response = client.post("/notifications/clear?user_id=farmer-b", headers=_headers())

    assert read_response.status_code == 200
    assert read_response.json() == {"notifications": notices, "unread_count": 1}
    assert mark_response.status_code == 200
    assert clear_response.status_code == 200
    assert read_uids == ["farmer-a"]
    assert marked_uids == ["farmer-a"]
    assert cleared_uids == ["farmer-a"]


@pytest.mark.parametrize(("method", "path"), [
    ("get", "/notifications"),
    ("post", "/notifications/mark-read"),
    ("post", "/notifications/clear"),
    ("get", "/auth/user"),
])
def test_private_routes_reject_missing_tokens(client, monkeypatch, method, path):
    calls = []
    monkeypatch.setattr(notifications_api, "get_user_notifications", lambda uid: calls.append(uid))
    monkeypatch.setattr(notifications_api, "mark_notifications_read", lambda uid: calls.append(uid))
    monkeypatch.setattr(notifications_api, "clear_notifications", lambda uid: calls.append(uid))

    response = client.request(method, path)

    assert response.status_code in {401, 403}
    assert calls == []


@pytest.mark.parametrize(("method", "path"), [
    ("get", "/notifications"),
    ("post", "/notifications/mark-read"),
    ("post", "/notifications/clear"),
    ("get", "/auth/user"),
])
def test_private_routes_reject_expired_tokens(client, monkeypatch, method, path):
    expired = type("ExpiredIdTokenError", (Exception,), {})("expired offline test token")
    _set_bearer_result(monkeypatch, expired)

    response = client.request(method, path, headers=_headers("expired-token"))

    assert response.status_code == 401


@pytest.mark.parametrize(("method", "path"), [
    ("get", "/notifications"),
    ("post", "/notifications/mark-read"),
    ("post", "/notifications/clear"),
    ("get", "/auth/user"),
])
def test_private_routes_reject_fallback_even_if_setting_is_enabled(
    client, monkeypatch, method, path
):
    monkeypatch.setattr(firebase_config, "ALLOW_UNVERIFIED_FIREBASE_TOKENS", True)
    _set_bearer_result(monkeypatch, {
        "uid": "forged-farmer",
        "email": "forged@example.test",
        "token_verified_offline": True,
    })

    response = client.request(method, path, headers=_headers("unsigned-token"))

    assert response.status_code == 401


def test_auth_verification_accepts_a_valid_firebase_token(client, monkeypatch):
    verified_claims = {"uid": "farmer-a", "email": "a@example.test"}
    _set_body_token_result(monkeypatch, verified_claims)
    _set_bearer_result(monkeypatch, verified_claims)

    verify_response = client.post("/auth/verify-token", json={"token": "valid-token"})
    user_response = client.get("/auth/user", headers=_headers())

    assert verify_response.status_code == 200
    assert verify_response.json()["uid"] == "farmer-a"
    assert verify_response.json()["message"] == "Token verified successfully"
    assert user_response.status_code == 200
    assert user_response.json()["uid"] == "farmer-a"
    assert user_response.json()["authenticated"] is True


def test_auth_verification_rejects_expired_token(client, monkeypatch):
    expired = type("ExpiredIdTokenError", (Exception,), {})("expired offline test token")
    _set_body_token_result(monkeypatch, expired)

    response = client.post("/auth/verify-token", json={"token": "expired-token"})

    assert response.status_code == 401


def test_auth_verification_rejects_unsigned_fallback_identity(client, monkeypatch):
    monkeypatch.setattr(firebase_config, "ALLOW_UNVERIFIED_FIREBASE_TOKENS", True)
    _set_body_token_result(monkeypatch, {
        "uid": "forged-farmer",
        "email": "forged@example.test",
        "token_verified_offline": True,
    })

    response = client.post("/auth/verify-token", json={"token": "unsigned-token"})

    assert response.status_code == 401


def test_auth_verification_rejects_missing_token(client):
    response = client.post("/auth/verify-token", json={})
    assert response.status_code == 422


def test_password_recovery_requires_matching_account_details(client, monkeypatch):
    monkeypatch.setattr(auth_api, "_recovery_details_match", lambda email, mobile: False)

    response = client.post("/auth/recovery/verify", json={
        "email": "unknown@example.test",
        "mobile_number": "09123456789",
        "new_password": "secure1",
        "confirm_new_password": "secure1",
    })

    assert response.status_code == 200
    assert response.json() == {
        "verified": False,
        "message": "We couldn't verify those recovery details. Please check them and try again.",
    }


def test_password_recovery_accepts_verified_match_and_normalizes_phone(client, monkeypatch):
    received = []
    monkeypatch.setattr(
        auth_api,
        "_recovery_details_match",
        lambda email, mobile: received.append((email, mobile)) or True,
    )

    response = client.post("/auth/recovery/verify", json={
        "email": "FARMER@EXAMPLE.TEST",
        "mobile_number": "+63 912 345 6789",
        "new_password": "secure1",
        "confirm_new_password": "secure1",
    })

    assert response.status_code == 200
    assert response.json()["verified"] is True
    assert received == [("farmer@example.test", "+63 912 345 6789")]
    assert auth_api._normalize_mobile_number("09 123 456 789") == "09123456789"
    assert auth_api._normalize_mobile_number("+63 912 345 6789") == "09123456789"


def test_password_recovery_rejects_mismatched_new_passwords_without_lookup(client, monkeypatch):
    called = []
    monkeypatch.setattr(auth_api, "_recovery_details_match", lambda email, mobile: called.append(True))

    response = client.post("/auth/recovery/verify", json={
        "email": "farmer@example.test",
        "mobile_number": "09123456789",
        "new_password": "secure1",
        "confirm_new_password": "different1",
    })

    assert response.status_code == 200
    assert response.json()["verified"] is False
    assert called == []
