"""Canary routing guards must protect Render before multipart parsing begins."""
import asyncio

from fastapi.testclient import TestClient
from starlette.requests import Request

from app import main
from app.api import hybrid_uploads
from app.core import config
from app.core import hybrid_canary
from app.core.security import verify_verified_firebase_token


def _enable_one(monkeypatch):
    monkeypatch.setattr(config, "HYBRID_CANARY_ENABLED", True)
    monkeypatch.setattr(config, "HYBRID_UPLOAD_ROUTES_ENABLED", True)
    monkeypatch.setattr(config, "HYBRID_CANARY_USER_UIDS", frozenset({"canary-user"}))


def _legacy_request():
    return Request({
        "type": "http", "method": "POST", "scheme": "https",
        "path": "/detect/image", "query_string": b"",
        "headers": [(b"authorization", b"Bearer test-token")],
    })


def test_only_allowlisted_firebase_uid_receives_hybrid_capability(monkeypatch):
    _enable_one(monkeypatch)
    assert hybrid_canary.is_hybrid_canary_user("canary-user")
    assert hybrid_canary.is_hybrid_upload_user("canary-user")
    assert not hybrid_canary.is_hybrid_canary_user("another-farmer")
    assert not hybrid_canary.is_hybrid_upload_user("another-farmer")
    monkeypatch.setattr(config, "HYBRID_UPLOAD_ROUTES_ENABLED", False)
    assert not hybrid_canary.is_hybrid_canary_user("canary-user")
    assert not hybrid_canary.is_hybrid_upload_user("canary-user")


def test_hybrid_route_handler_rejects_non_canary_even_when_mounted(monkeypatch):
    _enable_one(monkeypatch)
    monkeypatch.setattr(hybrid_uploads, "HYBRID_UPLOAD_ROUTES_ENABLED", True)
    try:
        hybrid_uploads._enabled({"uid": "another-farmer"})
        assert False, "non-canary must not pass the server-side gate"
    except Exception as error:
        assert getattr(error, "status_code", None) == 404


def test_canary_legacy_upload_is_rejected_before_multipart_parsing(monkeypatch):
    _enable_one(monkeypatch)
    monkeypatch.setattr(main, "HYBRID_CANARY_ENABLED", True)
    monkeypatch.setattr("app.infrastructure.firebase.auth.verify_token", lambda token: {
        "uid": "canary-user", "token_verified_offline": False,
    })
    async def inline(function, *args):
        return function(*args)
    monkeypatch.setattr(main, "run_in_threadpool", inline)
    # The request has no receive channel/body.  A 409 therefore proves this
    # header-only helper does not attempt multipart parsing or body reads.
    response = asyncio.run(main._canary_legacy_upload_response(_legacy_request()))
    assert response is not None and response.status_code == 409
    # Exercise the registered middleware too.  Invalid multipart cannot reach
    # FastAPI's UploadFile dependency for this allowlisted UID.
    http = TestClient(main.create_app())
    actual = http.post(
        "/detect/image", content=b"not-a-multipart-upload",
        headers={"Authorization": "Bearer test-token", "Content-Type": "multipart/form-data"},
    )
    assert actual.status_code == 409


def test_non_canary_legacy_upload_is_not_intercepted_by_canary_middleware(monkeypatch):
    _enable_one(monkeypatch)
    monkeypatch.setattr(main, "HYBRID_CANARY_ENABLED", True)
    monkeypatch.setattr("app.infrastructure.firebase.auth.verify_token", lambda token: {
        # The legacy endpoint rejects this identity normally, before model
        # import.  The assertion is only that it was not canary-blocked.
        "uid": "another-farmer", "token_verified_offline": True,
    })
    async def inline(function, *args):
        return function(*args)
    monkeypatch.setattr(main, "run_in_threadpool", inline)
    response = asyncio.run(main._canary_legacy_upload_response(_legacy_request()))
    assert response is None


def test_authenticated_feature_projection_and_rollback(monkeypatch):
    _enable_one(monkeypatch)
    app = main.create_app()
    identity = {"uid": "canary-user", "email": "farmer@example.test", "email_verified": True}
    app.dependency_overrides[verify_verified_firebase_token] = lambda: identity
    client = TestClient(app)
    assert client.get("/auth/user").json()["features"] == {"hybrid_uploads": True}
    identity["uid"] = "another-farmer"
    assert client.get("/auth/user").json()["features"] == {"hybrid_uploads": False}
    identity["uid"] = "canary-user"
    identity["role"] = "expert"
    assert client.get("/auth/user").json()["features"] == {"hybrid_uploads": False}
    identity.pop("role")
    monkeypatch.setattr(config, "HYBRID_UPLOAD_ROUTES_ENABLED", False)
    assert client.get("/auth/user").json()["features"] == {"hybrid_uploads": False}
