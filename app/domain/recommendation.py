"""
app/domain/recommendation.py — Recommendation loading, saving, and building.

Reads/writes local JSON files and calls the ML detector singleton.
No HTTP, no Firebase.
"""
import json
import logging
import math
from typing import Optional

from app.core.config import EXPERT_RECOMMENDATIONS_PATH

logger = logging.getLogger(__name__)


def _get_firestore():
    try:
        from firebase_admin import firestore  # noqa: PLC0415
        return firestore.client()
    except Exception as error:
        logger.warning("Firestore recommendation store unavailable: %s", error)
        return None


def load_expert_recommendations() -> dict:
    """Load expert-edited overrides from Firestore, then the offline cache."""
    firestore_client = _get_firestore()
    if firestore_client:
        try:
            snapshot = firestore_client.collection("app_settings").document("expert_recommendations").get()
            if snapshot.exists:
                data = (snapshot.to_dict() or {}).get("overrides", {})
                if isinstance(data, dict):
                    return data
        except Exception as error:
            logger.warning("Could not load Firestore recommendations: %s", error)
    if not EXPERT_RECOMMENDATIONS_PATH.exists():
        return {}
    try:
        with open(EXPERT_RECOMMENDATIONS_PATH, "r", encoding="utf-8") as file:
            data = json.load(file)
            return data if isinstance(data, dict) else {}
    except Exception as error:
        logger.warning("Could not load expert recommendations: %s", error)
        return {}


def save_expert_recommendations(data: dict) -> None:
    """Persist overrides to Firestore; use the local file only offline."""
    firestore_client = _get_firestore()
    if firestore_client:
        try:
            firestore_client.collection("app_settings").document("expert_recommendations").set({"overrides": data})
            return
        except Exception as error:
            logger.warning("Could not save Firestore recommendations: %s", error)
    with open(EXPERT_RECOMMENDATIONS_PATH, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=True)


def normalize_disease_key(disease_name: str) -> str:
    """Create a stable dictionary key for recommendation overrides."""
    return str(disease_name or "").strip().lower().replace(" ", "_")


def apply_expert_recommendation_override(base_recommendation: dict, disease_name: str) -> dict:
    """Merge any expert override into the recommendation payload."""
    overrides = load_expert_recommendations()
    override = overrides.get(normalize_disease_key(disease_name))
    if not override or not override.get("active", True):
        return base_recommendation

    merged = dict(base_recommendation)
    recommendation_data = dict(base_recommendation.get("recommendations", {}))
    recommendation_data["fertilizer"] = override.get("fertilizer", recommendation_data.get("fertilizer"))
    recommendation_data["treatment"] = override.get("treatment", recommendation_data.get("treatment"))
    prevention = override.get("prevention")
    if isinstance(prevention, list) and prevention:
        recommendation_data["prevention"] = prevention
    merged["recommendations"] = recommendation_data
    note = override.get("note")
    if note:
        merged["note"] = f"{merged.get('note', '')} | Expert note: {note}".strip(" |")
    merged["expert_override"] = override
    merged["source"] = "expert_override"
    return merged


def build_recommendation_response(
    disease_name: str,
    confidence: float,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    accuracy: Optional[float] = None,
) -> dict:
    """Build the default recommendation payload for one detection."""
    # Lazy import to avoid circular dependencies at module load time.
    from model import detector  # noqa: PLC0415

    if not disease_name or not isinstance(disease_name, str):
        raise ValueError("Invalid disease name provided")
    if not isinstance(confidence, (int, float)) or math.isnan(confidence) or math.isinf(confidence):
        raise ValueError("Invalid confidence value provided")

    user_location = None
    if lat is not None and lng is not None:
        user_location = {
            "lat": lat,
            "lng": lng,
            "accuracy": accuracy if accuracy else 10.0,
        }

    recommendations = detector.get_fertilizer_recommendation(
        disease_name=disease_name,
        confidence=confidence,
        gps_data=None,
        user_location=user_location,
    )

    prevention = recommendations.get("prevention", [])
    if isinstance(prevention, str):
        prevention = [prevention]

    return {
        "disease": recommendations["disease"],
        "confidence_percent": recommendations["confidence"],
        "model_used": recommendations.get("model", "Unknown"),
        "recommendations": {
            "fertilizer": recommendations["fertilizer"],
            "treatment": recommendations["treatment"],
            "prevention": prevention,
        },
        "location": recommendations.get("location", {}),
        "note": recommendations["note"],
        "source": "default",
    }
