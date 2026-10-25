"""Shared validation for detector responses.

The detector deliberately reports a structured failed status instead of
pretending that a backend exception is a valid empty prediction.  This module
keeps endpoint handling consistent while accepting legacy successful detector
responses that predate the status field.
"""

from __future__ import annotations

from typing import Any
from threading import Lock

_INFERENCE_LOCK = Lock()

import numpy as np


class InferenceUnavailable(RuntimeError):
    """Raised when a detector could not analyse an image reliably."""


def run_image_inference(detector: Any, image: np.ndarray, confidence: float, *,
                        annotate: bool = True) -> dict[str, Any]:
    """Run inference and reject failed or malformed detector responses.

    An empty ``detections`` list with a successful status is a legitimate
    no-detection result.  A failed status, a thrown exception, or a malformed
    response is not an analysis result and must never be persisted as one.
    """
    try:
        # The shared detector can be called by concurrent upload worker threads.
        with _INFERENCE_LOCK:
            result = (detector.predict(image, conf=confidence) if annotate else
                      detector.predict(image, conf=confidence, annotate=False))
    except Exception as exc:
        raise InferenceUnavailable("Detector execution failed") from exc

    if not isinstance(result, dict):
        raise InferenceUnavailable("Detector returned an invalid response")
    if str(result.get("inference_status", "success")).lower() != "success":
        raise InferenceUnavailable("Detector reported an inference failure")
    if not isinstance(result.get("detections"), list):
        raise InferenceUnavailable("Detector returned invalid detections")
    if not isinstance(result.get("image"), np.ndarray):
        raise InferenceUnavailable("Detector did not return an annotated image")
    return result
