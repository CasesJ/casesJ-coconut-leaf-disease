"""
app/schemas/expert.py — Expert-facing request body shapes.
"""
from pydantic import BaseModel

from app.core.config import VERIFIED_STATUS


class ExpertRecommendationUpdate(BaseModel):
    disease: str
    fertilizer: str
    treatment: str
    prevention: list[str]
    note: str | None = None
    active: bool = True
    target_user_id: str | None = None
    target_record_id: str | None = None


class VerificationUpdate(BaseModel):
    status: str = VERIFIED_STATUS
