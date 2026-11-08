"""Roboflow Serverless adapter implementing CoconutAI's detector contract."""
from __future__ import annotations

import math
import tempfile
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.core import config
from app.domain.disease_labels import canonical_disease_label


class RoboflowInferenceError(RuntimeError):
    """A safe cloud-inference failure category for the API boundary."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _xyxy(value: dict[str, Any]) -> list[float] | None:
    source = value.get("bounding_box", value)
    if not isinstance(source, dict):
        return None
    if any(key in source for key in ("x1", "y1", "x2", "y2")):
        if not all(key in source for key in ("x1", "y1", "x2", "y2")):
            return None
        left, top, right, bottom = (_finite_number(source[key]) for key in ("x1", "y1", "x2", "y2"))
    elif all(key in source for key in ("x", "y", "width", "height")):
        x, y, width, height = (_finite_number(source[key]) for key in ("x", "y", "width", "height"))
        if None in (x, y, width, height):
            return None
        left, top, right, bottom = x - width / 2, y - height / 2, x + width / 2, y + height / 2
    else:
        return None
    if None in (left, top, right, bottom) or left < 0 or top < 0 or right <= left or bottom <= top:
        return None
    return [left, top, right, bottom]


def _prediction(value: Any, *, scale_x: float, scale_y: float) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    label = next((value.get(key) for key in ("class", "class_name", "label") if value.get(key) is not None), None)
    confidence = next((_finite_number(value.get(key)) for key in ("confidence", "score") if value.get(key) is not None), None)
    bbox = _xyxy(value)
    canonical_label = canonical_disease_label(label)
    if canonical_label is None or confidence is None or bbox is None:
        return None
    left, top, right, bottom = bbox
    return {
        "class": canonical_label,
        "confidence": round(confidence, 6),
        "bbox": [
            int(round(left * scale_x)), int(round(top * scale_y)),
            int(round(right * scale_x)), int(round(bottom * scale_y)),
        ],
    }


def _extract_predictions(payload: Any, *, scale_x: float, scale_y: float) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, float, tuple[int, int, int, int]]] = set()

    def walk(value: Any) -> None:
        prediction = _prediction(value, scale_x=scale_x, scale_y=scale_y)
        if prediction is not None:
            identity = (prediction["class"], prediction["confidence"], tuple(prediction["bbox"]))
            if identity not in seen:
                seen.add(identity)
                found.append(prediction)
            return
        if isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return found


def _cloud_error_category(error: Exception) -> str:
    status = getattr(error, "status_code", None)
    if status == 401:
        return "authentication"
    if status == 403:
        return "authorization"
    if status == 413:
        return "image_too_large"
    if status == 429:
        return "rate_limited"
    if isinstance(status, int) and 500 <= status <= 599:
        return "provider_unavailable"
    if type(error).__name__ == "HTTPServerConnectionError":
        return "network"
    return "provider_error"


class RoboflowDiseaseDetector:
    """Cloud-only detector; it never imports or initializes OpenVINO."""

    backend = "roboflow"
    active_model_path = None
    class_names = {
        0: "Caterpillars", 1: "Drying of Leaflets", 2: "Healthy",
        3: "Pestalotiopsis", 4: "Bud Rot", 5: "Stem Bleeding",
    }

    def __init__(self) -> None:
        if not config.ROBOFLOW_API_KEY:
            raise RuntimeError("ROBOFLOW_API_KEY is required when INFERENCE_BACKEND=roboflow")
        if not all((config.ROBOFLOW_API_URL, config.ROBOFLOW_WORKSPACE,
                    config.ROBOFLOW_WORKFLOW_ID, config.ROBOFLOW_IMAGE_INPUT_KEY)):
            raise RuntimeError("Roboflow workflow configuration is incomplete")
        from inference_sdk import InferenceConfiguration, InferenceHTTPClient

        self._client = InferenceHTTPClient(
            api_url=config.ROBOFLOW_API_URL, api_key=config.ROBOFLOW_API_KEY,
        ).configure(InferenceConfiguration(
            api_key_transport="header", workflow_run_retries_enabled=False,
        ))
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="roboflow-workflow")
        self._inflight: Future | None = None
        self._cooldown_until = 0.0

    @staticmethod
    def _temporary_jpeg(image: np.ndarray) -> tuple[Path, float, float, int]:
        if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3:
            raise RoboflowInferenceError("invalid_image")
        height, width = image.shape[:2]
        maximum = max(width, height)
        scale = min(1.0, config.ROBOFLOW_MAX_IMAGE_DIMENSION / maximum)
        target_width, target_height = max(1, round(width * scale)), max(1, round(height * scale))
        prepared = image if scale == 1.0 else cv2.resize(
            image, (target_width, target_height), interpolation=cv2.INTER_AREA,
        )
        buffer = None
        for quality in (90, 82, 75, 68):
            encoded, candidate = cv2.imencode(".jpg", prepared, [cv2.IMWRITE_JPEG_QUALITY, quality])
            if encoded and len(candidate) <= config.ROBOFLOW_MAX_IMAGE_BYTES:
                buffer = candidate
                break
        if buffer is None:
            raise RoboflowInferenceError("image_too_large")
        with tempfile.NamedTemporaryFile(prefix="coconutai-roboflow-", suffix=".jpg", delete=False) as output:
            output.write(buffer.tobytes())
            path = Path(output.name)
        # cv2 encodes pixels only; the temporary JPEG has no source EXIF/GPS/XMP.
        return path, width / target_width, height / target_height, path.stat().st_size

    def _run_workflow(self, path: Path) -> Any:
        return self._client.run_workflow(
            workspace_name=config.ROBOFLOW_WORKSPACE,
            workflow_id=config.ROBOFLOW_WORKFLOW_ID,
            images={config.ROBOFLOW_IMAGE_INPUT_KEY: str(path)},
            use_cache=True,
        )

    def predict(self, image: np.ndarray, conf: float = 0.05, *, annotate: bool = True) -> dict[str, Any]:
        del conf  # The deployed workflow owns model thresholds; app thresholds remain display/persistence policy.
        temporary_path, scale_x, scale_y, temporary_bytes = self._temporary_jpeg(image)
        request: Future | None = None
        try:
            with self._lock:
                now = time.monotonic()
                if now < self._cooldown_until:
                    raise RoboflowInferenceError("cooldown")
                if self._inflight is not None and not self._inflight.done():
                    raise RoboflowInferenceError("request_in_progress")
                self._inflight = self._executor.submit(self._run_workflow, temporary_path)
                request = self._inflight
            try:
                response = request.result(timeout=config.ROBOFLOW_TIMEOUT_SECONDS)
            except TimeoutError as error:
                # Do not submit another request while the SDK call may still be in flight.
                with self._lock:
                    self._cooldown_until = time.monotonic() + config.ROBOFLOW_FAILURE_COOLDOWN_SECONDS
                raise RoboflowInferenceError("timeout") from error
            except Exception as error:
                category = _cloud_error_category(error)
                if category in {"rate_limited", "provider_unavailable", "network", "provider_error"}:
                    with self._lock:
                        self._cooldown_until = time.monotonic() + config.ROBOFLOW_FAILURE_COOLDOWN_SECONDS
                raise RoboflowInferenceError(category) from error
            finally:
                if request is not None and request.done():
                    with self._lock:
                        if self._inflight is request:
                            self._inflight = None
            detections = _extract_predictions(response, scale_x=scale_x, scale_y=scale_y)
            output = image.copy() if annotate else image
            if annotate:
                for detection in detections:
                    x1, y1, x2, y2 = detection["bbox"]
                    cv2.rectangle(output, (x1, y1), (x2, y2), (0, 60, 220), 4)
            return {"detections": detections, "image": output, "inference_status": "success"}
        finally:
            temporary_path.unlink(missing_ok=True)
