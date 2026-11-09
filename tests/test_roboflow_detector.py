"""Offline contract tests for the Roboflow cloud detector adapter."""
from __future__ import annotations

from concurrent.futures import Future
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from app.core import config
from app.domain.disease_labels import canonical_disease_label
from app.infrastructure.ml.roboflow_detector import (
    RoboflowDiseaseDetector,
    RoboflowInferenceError,
    UnsupportedRoboflowClassError,
    _extract_predictions,
)


def test_cloud_response_is_normalized_and_maps_grey_leaf_spot_to_current_label():
    payload = [{"outputs": {"predictions": [
        {"class": "bud root", "confidence": 0.964844, "x": 100, "y": 50, "width": 40, "height": 20},
        {"class": "grey_leaf_spot", "confidence": 0.9, "x": 20, "y": 20, "width": 10, "height": 10},
    ]}}]

    assert _extract_predictions(payload, scale_x=2.0, scale_y=3.0) == [
        {"class": "Bud Rot", "confidence": 0.964844, "bbox": [160, 120, 240, 180]},
        {"class": "Grey Leaf Spot", "confidence": 0.9, "bbox": [30, 45, 50, 75]},
    ]


def test_label_mapping_covers_supported_cloud_classes():
    assert canonical_disease_label("bud root") == "Bud Rot"
    assert canonical_disease_label("Stem_Bleeding") == "Stem Bleeding"
    assert canonical_disease_label("Grey Leaf Spot") == "Grey Leaf Spot"
    assert canonical_disease_label("Pestalotiopsis") == "Grey Leaf Spot"
    assert canonical_disease_label("Cercospora") is None


def test_unknown_structurally_valid_cloud_class_is_not_a_successful_zero_detection():
    payload = {"predictions": [{
        "class": "future disease", "confidence": 0.9,
        "x": 20, "y": 20, "width": 5, "height": 5,
    }]}

    with pytest.raises(UnsupportedRoboflowClassError, match="future disease"):
        _extract_predictions(payload, scale_x=1.0, scale_y=1.0)


def test_temporary_cloud_jpeg_is_bounded_and_has_no_metadata(monkeypatch):
    monkeypatch.setattr(config, "ROBOFLOW_MAX_IMAGE_DIMENSION", 1600)
    image = np.random.default_rng(4).integers(0, 256, (2400, 3200, 3), dtype=np.uint8)
    path, scale_x, scale_y, byte_size = RoboflowDiseaseDetector._temporary_jpeg(image)
    try:
        decoded = cv2.imread(str(path))
        assert decoded.shape[:2] == (1200, 1600)
        assert scale_x == pytest.approx(2.0)
        assert scale_y == pytest.approx(2.0)
        assert byte_size <= config.ROBOFLOW_MAX_IMAGE_BYTES
    finally:
        path.unlink(missing_ok=True)


def test_timeout_prevents_a_second_paid_request_while_first_is_inflight(monkeypatch):
    monkeypatch.setattr(config, "ROBOFLOW_TIMEOUT_SECONDS", 1)
    detector = object.__new__(RoboflowDiseaseDetector)
    detector._lock = __import__("threading").Lock()
    detector._cooldown_until = 0.0
    future = SimpleNamespace(done=lambda: False)
    detector._inflight = future
    detector._executor = None
    with pytest.raises(RoboflowInferenceError, match="request_in_progress"):
        detector.predict(np.zeros((16, 16, 3), dtype=np.uint8), annotate=False)


def test_provider_failure_is_reported_as_a_safe_category():
    class ProviderFailure(Exception):
        status_code = 503

    class FailedExecutor:
        def submit(self, *_args):
            future = Future()
            future.set_exception(ProviderFailure("private provider response"))
            return future

    detector = object.__new__(RoboflowDiseaseDetector)
    detector._lock = __import__("threading").Lock()
    detector._cooldown_until = 0.0
    detector._inflight = None
    detector._executor = FailedExecutor()

    with pytest.raises(RoboflowInferenceError, match="provider_unavailable"):
        detector.predict(np.zeros((16, 16, 3), dtype=np.uint8), annotate=False)


def test_cloud_adapter_source_never_imports_openvino():
    source = ("app/infrastructure/ml/roboflow_detector.py")
    assert "import openvino" not in open(source, encoding="utf-8").read().lower()


def test_model_selects_cloud_adapter_without_constructing_local_detector():
    source = open("model.py", encoding="utf-8").read()
    assert 'if INFERENCE_BACKEND == "roboflow":' in source
    assert "detector = RoboflowDiseaseDetector()" in source
