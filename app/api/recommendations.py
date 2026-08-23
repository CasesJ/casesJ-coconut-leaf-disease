"""
app/api/recommendations.py — Fertilizer/treatment recommendation endpoint.
"""
import math
import logging
from typing import Optional

from fastapi import APIRouter

from app.domain.recommendation import build_recommendation_response
from app.schemas.recommendation import RecommendationRequest

router = APIRouter(tags=["recommendations"])
logger = logging.getLogger(__name__)


@router.post("/recommendations/fertilizer")
async def get_recommendations(
    request: RecommendationRequest,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    accuracy: Optional[float] = None,
):
    """Get fertilizer, treatment, and prevention recommendations for a detected disease."""
    try:
        if not request.disease or not isinstance(request.disease, str):
            return {
                "disease": "Error", "confidence_percent": 0, "model_used": "Error",
                "recommendations": {"fertilizer": "Invalid disease name", "treatment": "Please provide a valid disease name", "prevention": ["Ensure detection data is valid"]},
                "location": {}, "note": "Error: Invalid disease parameter",
            }
        if not isinstance(request.confidence, (int, float)) or request.confidence is None:
            return {
                "disease": request.disease, "confidence_percent": 0, "model_used": "Error",
                "recommendations": {"fertilizer": "Unable to generate recommendations", "treatment": "Invalid confidence value provided", "prevention": ["Monitor tree health regularly"]},
                "location": {}, "note": "Error: Invalid confidence value",
            }
        if math.isnan(request.confidence) or math.isinf(request.confidence):
            return {
                "disease": request.disease, "confidence_percent": 0, "model_used": "Error",
                "recommendations": {"fertilizer": "Unable to generate recommendations", "treatment": "Confidence value is invalid (NaN or Infinity)", "prevention": ["Monitor tree health regularly"]},
                "location": {}, "note": "Error: Invalid confidence value",
            }
        return build_recommendation_response(
            disease_name=request.disease,
            confidence=request.confidence,
            lat=lat, lng=lng, accuracy=accuracy,
        )
    except Exception as exc:
        logger.error("Recommendation error: %s", exc, exc_info=True)
        return {
            "disease": request.disease if request else "Unknown",
            "confidence_percent": request.confidence if request else 0,
            "model_used": "Error",
            "recommendations": {"fertilizer": "Unable to generate recommendations", "treatment": "Please consult a local agricultural expert", "prevention": ["Monitor tree health regularly"]},
            "location": {}, "note": f"Error: {exc}",
        }
