from pydantic import BaseModel
from typing import List

class Detection(BaseModel):
    class_name: str
    confidence: float
    bbox: List[int]  # [x1, y1, x2, y2]

class DetectionResponse(BaseModel):
    detections: List[Detection]
    count: int
    annotated_image_base64: str  # base64-encoded JPEG