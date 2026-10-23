"""Supporting geographic evidence. No function in this module decides identity."""
import json
import math

from app.core.config import TREE_IDENTITY_GPS_TOLERANCE_METERS


def normalize_gps(value) -> dict | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return None
    if not isinstance(value, dict):
        return None
    try:
        if isinstance(value.get("latitude"), bool) or isinstance(value.get("longitude"), bool):
            return None
        lat, lon = float(value["latitude"]), float(value["longitude"])
    except (KeyError, ValueError, TypeError, OverflowError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    # Legacy accuracy may be EXIF DOP or an invented default. Only accept an
    # explicitly metre-valued field, never the legacy `accuracy` field.
    accuracy = value.get("accuracy_m")
    if (isinstance(accuracy, bool) or not isinstance(accuracy, (float, int))
            or not math.isfinite(accuracy) or accuracy < 0):
        accuracy = None
    return {"latitude": lat, "longitude": lon, "accuracy_m": accuracy,
            "source": str(value.get("source") or "unknown")}


def compare_gps(baseline, follow_up, tolerance_m=None) -> dict:
    tolerance = float(TREE_IDENTITY_GPS_TOLERANCE_METERS if tolerance_m is None else tolerance_m)
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("GPS tolerance must be positive and finite")
    first, second = normalize_gps(baseline), normalize_gps(follow_up)
    result = {"status": "gps_unavailable", "distance_m": None, "tolerance_m": tolerance,
              "baseline": first, "follow_up": second, "accuracy_known": False}
    if first is None or second is None:
        return result
    lat1, lat2 = math.radians(first["latitude"]), math.radians(second["latitude"])
    dlat, dlon = lat2 - lat1, math.radians(second["longitude"] - first["longitude"])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    distance = 6_371_008.8 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, h))))
    result.update(distance_m=round(distance, 2),
                  status="within_tolerance" if distance <= tolerance else "outside_tolerance")
    accuracies = (first["accuracy_m"], second["accuracy_m"])
    if all(accuracy is not None for accuracy in accuracies):
        result["accuracy_known"] = True
        uncertainty = sum(accuracies)
        result["combined_accuracy_m"] = uncertainty
        if uncertainty > 0 and distance - uncertainty <= tolerance < distance + uncertainty:
            result["status"] = "inconclusive_accuracy"
    return result


def identity_evidence(case: dict, follow_up: dict) -> dict:
    baseline_ref = case.get("tree_reference") or {}
    follow_ref = follow_up.get("tree_reference") or {}
    baseline = normalize_gps(baseline_ref.get("gps_data"))
    current = normalize_gps(follow_ref.get("gps_data"))
    baseline_source = "baseline_whole_tree" if baseline else "baseline_disease"
    current_source = "follow_up_whole_tree" if current else "follow_up_disease"
    comparison = compare_gps(baseline or case.get("baseline_gps_snapshot"),
                             current or follow_up.get("gps_data"))
    return {**comparison, "baseline_evidence": baseline_source,
            "follow_up_evidence": current_source}
