"""Inference result classification must not confuse failures with empty results."""

import numpy as np
import pytest

from app.services.inference_service import InferenceUnavailable, run_image_inference


class _Detector:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def predict(self, _image, conf):
        assert conf == 0.05
        if self.error:
            raise self.error
        return self.result


def test_successful_empty_detection_result_is_valid():
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    result = run_image_inference(
        _Detector({"detections": [], "image": image, "inference_status": "success"}), image, 0.05
    )
    assert result["detections"] == []


@pytest.mark.parametrize("detector", [
    _Detector({"detections": [], "image": np.zeros((8, 8, 3), dtype=np.uint8), "inference_status": "failed"}),
    _Detector(error=RuntimeError("YOLO unavailable")),
    _Detector({"detections": "not-a-list", "image": np.zeros((8, 8, 3), dtype=np.uint8)}),
])
def test_failed_or_malformed_inference_is_rejected(detector):
    with pytest.raises(InferenceUnavailable):
        run_image_inference(detector, np.zeros((8, 8, 3), dtype=np.uint8), 0.05)
