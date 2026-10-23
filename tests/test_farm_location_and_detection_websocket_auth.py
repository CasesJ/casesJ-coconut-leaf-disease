"""Offline authentication and ownership tests for farm location and detection WS."""

import sys
import time
from types import ModuleType, SimpleNamespace

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import numpy as np
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
    import app.infrastructure.firebase.auth as firebase_auth

    def verify(token):
        if isinstance(result, Exception):
            raise result
        return dict(result)

    monkeypatch.setattr(firebase_auth, "verify_token", verify)
    monkeypatch.setattr(security, "verify_token", verify)


def _headers(token="farmer-token"):
    return {"Authorization": f"Bearer {token}"}


def test_farm_location_uses_verified_uid_and_ignores_foreign_user_id(client, monkeypatch):
    _set_token_result(monkeypatch, {"uid": "farmer-a"})
    requested_uids = []
    center = {"latitude": 7.2, "longitude": 125.6}
    monkeypatch.setattr(
        detections_api,
        "get_farm_center",
        lambda uid: requested_uids.append(uid) or center,
    )

    response = client.get("/farm/location?user_id=farmer-b", headers=_headers())

    assert response.status_code == 200
    assert response.json() == {"farm_center": center}
    assert requested_uids == ["farmer-a"]


def test_farm_location_rejects_missing_authentication(client, monkeypatch):
    requested_uids = []
    monkeypatch.setattr(
        detections_api,
        "get_farm_center",
        lambda uid: requested_uids.append(uid),
    )

    response = client.get("/farm/location")

    assert response.status_code in {401, 403}
    assert requested_uids == []


@pytest.mark.parametrize("token_result", [
    type("ExpiredIdTokenError", (Exception,), {})("expired token"),
    {"uid": "farmer-a", "token_verified_offline": True},
], ids=["expired-token", "unsigned-fallback"])
def test_farm_location_rejects_expired_and_unsigned_tokens(client, monkeypatch, token_result):
    _set_token_result(monkeypatch, token_result)
    requested_uids = []
    monkeypatch.setattr(
        detections_api,
        "get_farm_center",
        lambda uid: requested_uids.append(uid),
    )

    response = client.get("/farm/location", headers=_headers("bad-token"))

    assert response.status_code == 401
    assert requested_uids == []


def test_detection_websocket_rejects_missing_token_before_accept(client):
    with pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect("/detect/stream"):
            pytest.fail("Unauthenticated WebSocket unexpectedly connected")
    assert rejected.value.code == 1008


@pytest.mark.parametrize("token_result", [
    type("ExpiredIdTokenError", (Exception,), {})("expired token"),
    {"uid": "farmer-a", "token_verified_offline": True},
], ids=["expired-token", "unsigned-fallback"])
def test_detection_websocket_rejects_unverified_token_before_accept(
    client, monkeypatch, token_result
):
    _set_token_result(monkeypatch, token_result)
    acquired_connections = []

    async def acquire(key, limit):
        acquired_connections.append(key)

    monkeypatch.setattr(detections_api.limiter, "acquire_connection", acquire)

    with pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect("/detect/stream?token=bad-token"):
            pytest.fail("Unverified WebSocket unexpectedly connected")

    assert rejected.value.code == 1008
    assert acquired_connections == []


def test_detection_websocket_persists_stream_detection_to_firestore_flow_for_verified_farmer(client, monkeypatch):
    _set_token_result(monkeypatch, {"uid": "farmer-a", "email": "farmer@example.test"})
    acquired_connections = []
    released_connections = []
    persisted = []

    async def acquire(key, limit):
        acquired_connections.append(key)

    async def release(key):
        released_connections.append(key)

    monkeypatch.setattr(
        detections_api,
        "save_detection_record",
        lambda **record: persisted.append(record) or True,
    )
    monkeypatch.setattr(detections_api.limiter, "acquire_connection", acquire)
    monkeypatch.setattr(detections_api.limiter, "release_connection", release)

    model_module = ModuleType("model")
    model_module.detector = object()
    drone_module = ModuleType("drone_gps")
    drone_module.get_current_drone_position = lambda: None
    monkeypatch.setitem(sys.modules, "model", model_module)
    monkeypatch.setitem(sys.modules, "drone_gps", drone_module)

    image = np.zeros((2, 2, 3), dtype=np.uint8)
    monkeypatch.setattr(detections_api, "imdecode", lambda *args, **kwargs: image)
    monkeypatch.setattr(
        detections_api,
        "imencode",
        lambda *args, **kwargs: (True, np.array([1, 2, 3], dtype=np.uint8)),
    )
    monkeypatch.setattr(
        detections_api,
        "run_image_inference",
        lambda detector, frame, threshold: {
            "image": image,
            "detections": [{"class": "Bud Rot", "confidence": 0.9}],
        },
    )
    fake_clock = [0]

    def tick():
        fake_clock[0] += 2
        return fake_clock[0]

    monkeypatch.setattr(detections_api, "time", SimpleNamespace(time=tick))

    with client.websocket_connect(
        "/detect/stream?token=farmer-token&user_id=farmer-b"
    ) as websocket:
        websocket.send_text("data:image/jpeg;base64,YQ==")
        websocket.send_text("data:image/jpeg;base64,YQ==")
        result = websocket.receive_json()
        assert result["recorded"] is True
        time.sleep(0.2)

    assert acquired_connections == ["stream:farmer-a"]
    assert released_connections == ["stream:farmer-a"]
    assert len(persisted) == 1
    assert persisted[0]["user_id"] == "farmer-a"
    assert persisted[0]["source"] == "drone"
    assert persisted[0]["original_bytes"]
    assert persisted[0]["annotated_image"] is image
