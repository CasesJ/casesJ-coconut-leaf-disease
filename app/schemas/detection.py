"""
app/schemas/detection.py — Detection response-related Pydantic shapes.

Note: The SQLite-level DetectionRecord dataclass lives in
hybrid_storage.local_storage. This module holds the HTTP-layer shapes.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class DetectionResponse(BaseModel):
    """Shape of a single detection result returned to the client."""
    class_name: str
    confidence: float
    bbox: list[int]


class ImageDetectionResponse(BaseModel):
    """Full response from POST /detect/image."""
    detections: list[dict[str, Any]]
    recorded_count: int
    record_id: Optional[str]
    total_detected: int
    annotated_image_base64: str
    user_email: str
    message: str
    display_confidence_threshold: float
    record_confidence_threshold: float
    model: dict[str, Any]
    gps_lat: Optional[float]
    gps_lng: Optional[float]
    gps_accuracy: Optional[float]
    gps_source: str
    image_metadata: dict[str, Any]
