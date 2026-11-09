"""Offline checks for the isolated Roboflow response parser."""
from tools.test_roboflow_serverless_workflow import bbox_xyxy, extract_predictions


def test_converts_center_xywh_coordinates_to_xyxy():
    assert bbox_xyxy({"x": 50, "y": 40, "width": 20, "height": 10}) == [40.0, 35.0, 60.0, 45.0]


def test_preserves_valid_xyxy_coordinates():
    assert bbox_xyxy({"x1": 1, "y1": 2, "x2": 31, "y2": 42}) == [1.0, 2.0, 31.0, 42.0]


def test_rejects_malformed_or_non_finite_boxes():
    assert bbox_xyxy({"x": 10, "y": 10, "width": 0, "height": 3}) is None
    assert bbox_xyxy({"x1": 10, "y1": 10, "x2": 5, "y2": 20}) is None
    assert bbox_xyxy({"x": "nan", "y": 10, "width": 5, "height": 5}) is None
    assert bbox_xyxy({"bounding_box": "not-a-box"}) is None


def test_extracts_unique_valid_predictions_only():
    first = {"class": "Bud Rot", "confidence": 0.91, "x": 50, "y": 40, "width": 20, "height": 10}
    malformed = {"class": "Healthy", "confidence": 0.5, "x": 8, "y": 8, "width": -2, "height": 4}
    result = {"outputs": [{"predictions": [first, first, malformed]}]}

    assert extract_predictions(result) == [
        {"class": "Bud Rot", "confidence": 0.91, "bbox_xyxy": [40.0, 35.0, 60.0, 45.0]}
    ]
