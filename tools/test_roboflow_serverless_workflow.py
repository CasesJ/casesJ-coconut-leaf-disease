"""One-off local test for the hosted Roboflow workflow.

This script does not import CoconutAI application modules and makes no
database, Cloudinary, or local-model calls.  It sends a selected image to
Roboflow only when invoked explicitly with ``--allow-network``.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path
from typing import Any


API_URL = "https://serverless.roboflow.com"
WORKSPACE_NAME = "jamess-workspace-cjnrd"
WORKFLOW_ID = (
    "coconut-leaf-disease-j9noh-19-yolo26s-t1-v"
    "coconut-leaf-disease-j9noh-19-yolo26s-t1-logic"
)
IMAGE_INPUT_KEY = "image"


def _number(value: Any) -> float | None:
    """Return a finite numeric coordinate or confidence, rejecting booleans."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def bbox_xyxy(prediction: dict[str, Any]) -> list[float] | None:
    """Read a valid box and return [left, top, right, bottom] coordinates."""
    if "bounding_box" in prediction and not isinstance(prediction["bounding_box"], dict):
        return None
    source = prediction.get("bounding_box", prediction)

    xyxy_keys = ("x1", "y1", "x2", "y2")
    has_xyxy = any(key in source for key in xyxy_keys)
    if has_xyxy:
        if not all(key in source for key in xyxy_keys):
            return None
        left, top, right, bottom = (_number(source[key]) for key in xyxy_keys)
    else:
        xywh_keys = ("x", "y", "width", "height")
        if not all(key in source for key in xywh_keys):
            return None
        x, y, width, height = (_number(source[key]) for key in xywh_keys)
        if None in (x, y, width, height):
            return None
        left, top = x - width / 2, y - height / 2
        right, bottom = x + width / 2, y + height / 2

    if None in (left, top, right, bottom):
        return None
    if left < 0 or top < 0 or right <= left or bottom <= top:
        return None
    return [round(coordinate, 2) for coordinate in (left, top, right, bottom)]


def sanitize_prediction(value: Any) -> dict[str, Any] | None:
    """Keep only a class, confidence, and valid bounding box."""
    if not isinstance(value, dict):
        return None

    label = next(
        (value.get(key) for key in ("class", "class_name", "label") if value.get(key) is not None),
        None,
    )
    confidence = next(
        (_number(value.get(key)) for key in ("confidence", "score") if value.get(key) is not None),
        None,
    )
    bbox = bbox_xyxy(value)
    if not isinstance(label, str) or not label.strip() or confidence is None or bbox is None:
        return None

    return {
        "class": label.strip(),
        "confidence": round(confidence, 6),
        "bbox_xyxy": bbox,
    }


def extract_predictions(value: Any) -> list[dict[str, Any]]:
    """Extract unique, valid detections without returning workflow payload data."""
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, float, tuple[float, float, float, float]]] = set()

    def walk(item: Any) -> None:
        prediction = sanitize_prediction(item)
        if prediction is not None:
            identity = (
                prediction["class"],
                prediction["confidence"],
                tuple(prediction["bbox_xyxy"]),
            )
            if identity not in seen:
                seen.add(identity)
                found.append(prediction)
            return
        if isinstance(item, dict):
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return found


def _client(api_key: str):
    """Import the optional SDK only for an explicitly requested network run."""
    from inference_sdk import InferenceConfiguration, InferenceHTTPClient

    return InferenceHTTPClient(api_url=API_URL, api_key=api_key).configure(
        InferenceConfiguration(api_key_transport="header")
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--allow-network", action="store_true")
    args = parser.parse_args()

    if not args.allow_network:
        parser.error("Refusing network inference without --allow-network.")
    if not args.image.is_file():
        parser.error("The image must be a readable local file.")

    api_key = os.getenv("ROBOFLOW_API_KEY", "").strip()
    if not api_key:
        parser.error("ROBOFLOW_API_KEY is not set.")

    started = time.perf_counter()
    try:
        result = _client(api_key).run_workflow(
            workspace_name=WORKSPACE_NAME,
            workflow_id=WORKFLOW_ID,
            images={IMAGE_INPUT_KEY: str(args.image)},
            use_cache=True,
        )
    except Exception:
        print(json.dumps({"error": "workflow_request_failed"}))
        return 1

    print(json.dumps({
        "timing_ms": round((time.perf_counter() - started) * 1000, 1),
        "detections": extract_predictions(result),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
