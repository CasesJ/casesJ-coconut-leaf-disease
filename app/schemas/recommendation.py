"""
app/schemas/recommendation.py — Recommendation request and update shapes.
"""
from pydantic import BaseModel, ConfigDict


class RecommendationRequest(BaseModel):
    disease: str
    confidence: float

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "disease": "Cercospora",
                "confidence": 0.95,
            }
        }
    )


class FarmerRecommendationUpdate(BaseModel):
    """A farmer's low-confidence recommendation review (not expert verification)."""
    fertilizer: str
    treatment: str
    prevention: list[str]
    note: str | None = None
