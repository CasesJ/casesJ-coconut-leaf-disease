"""Uploaded detection records must not be created after an inference failure."""

import asyncio
import sys
from io import BytesIO
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi import HTTPException, UploadFile

from app.api import detections


def _image() -> bytes:
    success, encoded = cv2.imencode(".jpg", np.zeros((16, 16, 3), dtype=np.uint8))
    assert success
    return encoded.tobytes()


class _Gps:
    @staticmethod
    def extract_image_metadata(*_args):
        return {}

    @staticmethod
    def extract_gps_from_image(*_args):
        return None


def _configure_upload(monkeypatch, detector):
    async def allowed(*_args, **_kwargs):
        return None

    monkeypatch.setattr(detections.limiter, "rolling", allowed)
    monkeypatch.setattr(detections.limiter, "image_quota_24h", allowed)
    monkeypatch.setattr("app.infrastructure.firebase.auth.verify_token", lambda _token: {"uid": "farmer-a"})
    monkeypatch.setattr("drone_gps.get_drone_gps", lambda: _Gps())
    monkeypatch.setattr("drone_gps.init_drone_gps", lambda: _Gps())
    monkeypatch.setitem(sys.modules, "model", SimpleNamespace(detector=detector))


def _call_detect():
    return asyncio.run(detections.detect_image(
        request=SimpleNamespace(headers={"Authorization": "Bearer token"}),
        file=UploadFile(filename="leaf.jpg", file=BytesIO(_image()), headers={"content-type": "image/jpeg"}),
    ))


def test_failed_inference_does_not_create_detection_record(monkeypatch):
    saved = []
    detector = SimpleNamespace(predict=lambda image, conf: {
        "detections": [], "image": image, "inference_status": "failed",
    })
    _configure_upload(monkeypatch, detector)
    monkeypatch.setattr(detections, "save_detection_record", lambda *args, **kwargs: saved.append((args, kwargs)))

    with pytest.raises(HTTPException) as error:
        _call_detect()

    assert error.value.status_code == 503
    assert saved == []


def test_genuine_no_detection_result_remains_a_successful_analysis(monkeypatch):
    detector = SimpleNamespace(
        backend="test", active_model_path=None, class_names={},
        predict=lambda image, conf: {"detections": [], "image": image, "inference_status": "success"},
    )
    _configure_upload(monkeypatch, detector)
    monkeypatch.setattr(detections, "save_detection_record",
                        lambda *args, **kwargs: pytest.fail("No-detection result must not create a record"))

    result = _call_detect()

    assert result["detections"] == []
    assert result["recorded_count"] == 0
    assert result["message"] == "0 detections found (0 saved)"
