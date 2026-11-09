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
from app.domain.disease_labels import canonical_disease_key, canonical_disease_label

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
    canonical_label = canonical_disease_label(disease_name)
    label = canonical_label if canonical_label is not None else str(disease_name or "")
    return label.strip().lower().replace(" ", "_")


def _recommendation_override_keys(disease_name: str) -> tuple[str, ...]:
    """Prefer current override keys while retaining historic record overrides."""
    key = normalize_disease_key(disease_name)
    return (key, "pestalotiopsis") if key == "grey_leaf_spot" else (key,)


def get_expert_recommendation_override(overrides: dict, disease_name: str) -> dict | None:
    """Read current and historic expert override keys without migrating data."""
    if not isinstance(overrides, dict):
        return None
    return next((overrides.get(key) for key in _recommendation_override_keys(disease_name)
                 if isinstance(overrides.get(key), dict)), None)


def apply_expert_recommendation_override(base_recommendation: dict, disease_name: str) -> dict:
    """Merge any expert override into the recommendation payload."""
    overrides = load_expert_recommendations()
    override = get_expert_recommendation_override(overrides, disease_name)
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

    canonical_label = canonical_disease_label(disease_name)
    disease_key = canonical_disease_key(disease_name)
    if canonical_label is None or disease_key is None:
        raise ValueError("Unsupported disease name provided")
    confidence_percent = confidence if confidence > 1 else confidence * 100
    rules = {
        "bud rot": ("Do not use fertilizer as a cure. After removing active rot, restore palm vigor with organic matter and a soil-test-based coconut fertilizer program; avoid excess nitrogen on stressed palms.", "Remove infected parts and severely damaged coconut trees to prevent the spread of bud rot. Apply recommended fungicides, such as fosetyl-aluminum, to protect nearby healthy trees, following proper dosage and application guidelines.", "Improve drainage and air flow; clean the crown before the rainy season; remove and destroy infected nuts, fronds, and nearby alternate-host material; inspect nearby palms, especially within about 20 m, and avoid wounding the crown."),
        "caterpillars": ("Use NPK 10:10:10 balanced fertilizer monthly to boost tree vigor and recovery.", "PRIMARY: Spray Bacillus thuringiensis (Bt) at 1.5-2g per liter every 7 days for 3-4 weeks (organic option). ALTERNATIVE: Spinosad (0.5%) every 5-7 days or Phosphine (0.05%) for severe infestations. MANUAL: Handpick affected leaves in early morning when caterpillars are most active.", "Monitor leaves regularly for egg clusters and caterpillar droppings. Remove heavily infested fronds. Encourage natural predators like birds and parasitic wasps. Maintain tree vigor."),
        "drying of leaflets": ("Check soil and leaf nutrient status before fertilizing. Correct documented potassium, magnesium, or micronutrient deficiencies with a locally recommended coconut fertilizer program; do not apply high fertilizer rates to a severely stressed palm.", "Confirm whether the symptom is nutrient stress, drought, or pest injury before spraying. Remove only dead or heavily diseased fronds and use a locally registered fungicide only when a crop specialist confirms a fungal leaf disease.", "Maintain good drainage, mulch and water during dry periods, avoid crown and leaf injury, remove diseased debris, and monitor palms regularly so leaf symptoms are diagnosed early."),
        "stem bleeding": ("Use a soil-test-based fertilizer program to maintain palm vigor. Do not treat stem bleeding with fertilizer alone.", "Have the diagnosis confirmed promptly. Remove diseased bark only when advised by a qualified agricultural specialist, protect exposed tissue as recommended locally, and improve drainage around affected palms.", "Avoid trunk wounds, disinfect tools between palms, maintain drainage, remove severely affected palms when advised, and inspect nearby trees regularly for fresh bleeding lesions."),
        "healthy": ("Apply balanced NPK 12:12:12 every 3 months. Use phosphate rich during flowering season.", "No treatment needed. Continue regular monitoring.", "Maintain regular fertilization. Water consistently. Remove dead leaves. Monitor for pests."),
        "grey leaf spot": ("Use a soil-test-based coconut fertilizer program. Keep potassium and chloride adequate; PCA reports KCl nutrition can improve resistance to coconut fungal leaf spots. Fertilizer supports recovery but does not cure the infection.", "Mechanical Method: Remove and properly dispose of or burn severely infected fronds.\nChemical Method: Spray recommended fungicide only when needed.", "Use clean planting material, remove infected leaves promptly, keep nursery and field sanitation high, improve airflow and drainage, minimize leaf wounds, and monitor frequently in wet weather."),
    }
    fertilizer, treatment, prevention = rules[disease_key]
    location = ({"source": "browser_geolocation", "latitude": user_location["lat"], "longitude": user_location["lng"], "accuracy": user_location["accuracy"]} if user_location else {"source": "davao_default", "latitude": 7.0731, "longitude": 125.6123, "accuracy": 50000})
    confidence_note = " (High confidence - follow recommendations closely)" if confidence_percent >= 85 else (" (Good confidence - follow recommendations)" if confidence_percent >= 70 else " (Lower confidence - consult agricultural expert to confirm)")
    recommendations = {"disease": canonical_label, "confidence": round(confidence_percent, 2), "model": "Hardcoded", "fertilizer": fertilizer, "treatment": treatment, "prevention": [prevention], "location": location, "note": f"Davao region recommendations{confidence_note}. Location: {location['source'].replace('_', ' ')}"}

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
