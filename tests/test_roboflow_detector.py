"""Offline regression tests for the production Roboflow adapter."""
from types import SimpleNamespace

import numpy as np

from app.infrastructure.ml.roboflow import _confidence, _walk_predictions, RoboflowWorkflowDetector


def test_parser_preserves_unknown_labels_and_normalizes_percent_confidence():
    response = {"outputs": [{"predictions": [{
        "class": "Future disease", "confidence": 91, "x": 50, "y": 40, "width": 20, "height": 10,
    }]}]}
    assert _walk_predictions(response) == [{
        "class": "Future disease", "confidence": 0.91, "bbox": [40.0, 35.0, 60.0, 45.0],
    }]
    assert _confidence(101) is None


def test_detector_keeps_multiple_trees_and_clamps_boxes(monkeypatch):
    detector = object.__new__(RoboflowWorkflowDetector)
    detector.workspace, detector.workflow_id, detector.image_input_key = "workspace", "workflow", "image"
    detector._post = lambda *_, **__: SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"outputs": [{"predictions": [
            {"class": "Bud Rot", "confidence": .9, "x": 10, "y": 10, "width": 30, "height": 30},
            {"class": "Unknown future class", "confidence": .8, "x": 90, "y": 90, "width": 30, "height": 30},
        ]}]},
    )
    detector.url, detector.headers, detector.timeout_seconds = "", {}, 1
    image = np.zeros((100, 100, 3), dtype=np.uint8)

    result = detector.predict(image, conf=.05)

    assert result["inference_status"] == "success"
    assert result["detections"] == [
        {"class": "Bud Rot", "confidence": .9, "bbox": [0, 0, 25, 25]},
        {"class": "Unknown future class", "confidence": .8, "bbox": [75, 75, 100, 100]},
    ]


def test_malformed_workflow_result_is_failure_not_no_detection():
    detector = object.__new__(RoboflowWorkflowDetector)
    detector.workspace, detector.workflow_id, detector.image_input_key = "workspace", "workflow", "image"
    detector._post = lambda *_, **__: SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"error": "unexpected"})
    detector.url, detector.headers, detector.timeout_seconds = "", {}, 1

    result = detector.predict(np.zeros((8, 8, 3), dtype=np.uint8), conf=.05)

    assert result["inference_status"] == "failed"
    assert result["inference_error"] == "roboflow_invalid_response"
