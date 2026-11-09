"""Bounded Roboflow Serverless adapter used by the production upload path."""
from __future__ import annotations

import base64
import math
from typing import Any

import cv2
import numpy as np
import requests

from app.core import config


class RoboflowConfigurationError(RuntimeError):
    """The hosted workflow was selected without a complete configuration."""


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _confidence(value: Any) -> float | None:
    number = _number(value)
    if number is None or number < 0:
        return None
    # Workflows normally return 0..1.  Accept the documented percentage form
    # too, but reject values that cannot represent either scale.
    if number > 1:
        if number > 100:
            return None
        number /= 100
    return number


def _box(value: dict[str, Any]) -> list[float] | None:
    source = value.get("bounding_box", value)
    if not isinstance(source, dict):
        return None
    if any(key in source for key in ("x1", "y1", "x2", "y2")):
        if not all(key in source for key in ("x1", "y1", "x2", "y2")):
            return None
        left, top, right, bottom = (_number(source[key]) for key in ("x1", "y1", "x2", "y2"))
    else:
        if not all(key in source for key in ("x", "y", "width", "height")):
            return None
        x, y, width, height = (_number(source[key]) for key in ("x", "y", "width", "height"))
        if None in (x, y, width, height):
            return None
        left, top = x - width / 2, y - height / 2
        right, bottom = x + width / 2, y + height / 2
    if None in (left, top, right, bottom) or right <= left or bottom <= top:
        return None
    return [left, top, right, bottom]


def _walk_predictions(payload: Any) -> list[dict[str, Any]]:
    """Extract actual workflow predictions without assuming one output shape."""
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, float, tuple[int, int, int, int]]] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            label = next((value.get(key) for key in ("class", "class_name", "label") if value.get(key) is not None), None)
            score = next((_confidence(value.get(key)) for key in ("confidence", "score") if value.get(key) is not None), None)
            bbox = _box(value)
            if isinstance(label, str) and label.strip() and score is not None and bbox is not None:
                identity = (label.strip(), score, tuple(round(part) for part in bbox))
                if identity not in seen:
                    seen.add(identity)
                    found.append({"class": label.strip(), "confidence": score, "bbox": bbox})
                return
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return found


def _has_prediction_container(payload: Any) -> bool:
    """A random JSON error must not be recorded as a healthy photograph."""
    if isinstance(payload, dict):
        return (isinstance(payload.get("predictions"), list)
                or any(_has_prediction_container(child) for child in payload.values()))
    if isinstance(payload, list):
        return any(_has_prediction_container(child) for child in payload)
    return False


def _iou(left: list[int], right: list[int]) -> float:
    x1, y1 = max(left[0], right[0]), max(left[1], right[1])
    x2, y2 = min(left[2], right[2]), min(left[3], right[3])
    overlap = max(0, x2 - x1) * max(0, y2 - y1)
    union = (left[2] - left[0]) * (left[3] - left[1]) + (right[2] - right[0]) * (right[3] - right[1]) - overlap
    return overlap / union if union else 0.0


class RoboflowWorkflowDetector:
    backend = "roboflow-serverless"
    active_model_path = None
    class_names: dict[int, str] = {}

    def __init__(self) -> None:
        if not (config.ROBOFLOW_API_KEY and config.ROBOFLOW_WORKSPACE and config.ROBOFLOW_WORKFLOW_ID):
            raise RoboflowConfigurationError(
                "Roboflow inference requires ROBOFLOW_API_KEY, ROBOFLOW_WORKSPACE and ROBOFLOW_WORKFLOW_ID"
            )
        self.workspace = config.ROBOFLOW_WORKSPACE
        self.workflow_id = config.ROBOFLOW_WORKFLOW_ID
        self.image_input_key = config.ROBOFLOW_IMAGE_INPUT_KEY
        self.model_name = f"{self.workspace}/{self.workflow_id}"
        self.url = f"{config.ROBOFLOW_API_URL}/{self.workspace}/workflows/{self.workflow_id}"
        self.headers = {"Authorization": f"Bearer {config.ROBOFLOW_API_KEY}"}
        self.timeout_seconds = config.ROBOFLOW_TIMEOUT_SECONDS
        self._post = requests.post

    def predict(self, image: np.ndarray, conf: float = 0.05, *, annotate: bool = True) -> dict:
        del annotate  # The frontend draws saved metadata; no second full-size copy is needed.
        if not isinstance(image, np.ndarray) or image.ndim != 3:
            return {"detections": [], "image": image, "inference_status": "failed", "inference_error": "invalid_image"}
        # Encoding a fresh JPEG strips EXIF/XMP/GPS before it leaves CoconutAI.
        # The original upload remains available to the authorized evidence path.
        success, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not success:
            return {"detections": [], "image": image, "inference_status": "failed", "inference_error": "encode_failed"}
        try:
            response = self._post(
                self.url,
                headers=self.headers,
                json={
                    "use_cache": True,
                    "enable_profiling": False,
                    "inputs": {self.image_input_key: {
                        "type": "base64",
                        "value": base64.b64encode(encoded.tobytes()).decode("ascii"),
                    }},
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            response = response.json()
        except Exception:
            return {"detections": [], "image": image, "inference_status": "failed", "inference_error": "roboflow_request_failed"}

        if not _has_prediction_container(response):
            return {"detections": [], "image": image, "inference_status": "failed", "inference_error": "roboflow_invalid_response"}

        height, width = image.shape[:2]
        detections: list[dict[str, Any]] = []
        for item in _walk_predictions(response):
            if item["confidence"] < (conf / 100 if conf > 1 else conf):
                continue
            x1, y1, x2, y2 = item["bbox"]
            box = [max(0, round(x1)), max(0, round(y1)), min(width, round(x2)), min(height, round(y2))]
            if box[2] > box[0] and box[3] > box[1]:
                detections.append({"class": item["class"], "confidence": round(item["confidence"], 6), "bbox": box})
        # Workflows sometimes concatenate overlapping outputs.  Suppress only
        # same-label duplicates, never unknown labels or separate trees.
        retained: list[dict[str, Any]] = []
        for item in sorted(detections, key=lambda row: row["confidence"], reverse=True):
            if not any(item["class"] == other["class"] and _iou(item["bbox"], other["bbox"]) >= 0.45 for other in retained):
                retained.append(item)
        return {"detections": retained, "image": image, "inference_status": "success"}

    def model_info(self) -> dict[str, Any]:
        return {"name": "Roboflow Serverless workflow", "backend": self.backend, "workflow": self.model_name, "classes": self.class_names}
