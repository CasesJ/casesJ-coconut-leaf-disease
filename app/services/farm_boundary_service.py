"""Private, transaction-protected farm boundary storage and geometry helpers."""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite

MAX_VERTICES = 200
AREA_IDS = frozenset(range(1, 5))


class BoundaryError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finite(value: object, lower: float, upper: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise BoundaryError("Coordinates must be numbers") from exc
    if not isfinite(number) or not lower <= number <= upper:
        raise BoundaryError("Coordinates are outside their valid range")
    return number


def normalize_polygon(geometry: object) -> dict:
    """Validate and normalize a GeoJSON Polygon without a heavy GIS dependency."""
    if not isinstance(geometry, dict) or geometry.get("type") != "Polygon":
        raise BoundaryError("boundary_geojson must be a GeoJSON Polygon")
    rings = geometry.get("coordinates")
    if not isinstance(rings, list) or len(rings) != 1 or not isinstance(rings[0], list):
        raise BoundaryError("A single exterior polygon ring is required")
    ring = rings[0]
    if len(ring) < 3 or len(ring) > MAX_VERTICES + 1:
        raise BoundaryError(f"A boundary must have 3 to {MAX_VERTICES} vertices")
    points: list[list[float]] = []
    for pair in ring:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            raise BoundaryError("Each polygon position must contain longitude and latitude")
        points.append([_finite(pair[0], -180, 180), _finite(pair[1], -90, 90)])
    if points[0] == points[-1]:
        points.pop()
    if len(points) < 3 or len({tuple(point) for point in points}) < 3:
        raise BoundaryError("A boundary needs at least three distinct vertices")
    if _self_intersects(points):
        raise BoundaryError("Boundary sides must not cross")
    return {"type": "Polygon", "coordinates": [points + [points[0]]]} 


def _orientation(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a, b, point) -> bool:
    return (abs(_orientation(a, b, point)) < 1e-12
            and min(a[0], b[0]) - 1e-12 <= point[0] <= max(a[0], b[0]) + 1e-12
            and min(a[1], b[1]) - 1e-12 <= point[1] <= max(a[1], b[1]) + 1e-12)


def _segments_intersect(a, b, c, d) -> bool:
    ab_c, ab_d = _orientation(a, b, c), _orientation(a, b, d)
    cd_a, cd_b = _orientation(c, d, a), _orientation(c, d, b)
    if ((ab_c > 0 > ab_d or ab_c < 0 < ab_d)
            and (cd_a > 0 > cd_b or cd_a < 0 < cd_b)):
        return True
    return any((_on_segment(a, b, c), _on_segment(a, b, d),
                _on_segment(c, d, a), _on_segment(c, d, b)))


def _self_intersects(points: list[list[float]]) -> bool:
    total = len(points)
    for first in range(total):
        a, b = points[first], points[(first + 1) % total]
        for second in range(first + 1, total):
            if second in {first, (first + 1) % total} or (first == 0 and second == total - 1):
                continue
            c, d = points[second], points[(second + 1) % total]
            if _segments_intersect(a, b, c, d):
                return True
    return False


def point_in_polygon(latitude: object, longitude: object, geometry: object) -> bool:
    """Boundary-inclusive ray casting.  GeoJSON coordinates are [longitude, latitude]."""
    try:
        lat, lng = _finite(latitude, -90, 90), _finite(longitude, -180, 180)
        ring = normalize_polygon(geometry)["coordinates"][0]
    except BoundaryError:
        return False
    point = [lng, lat]
    inside = False
    for index in range(len(ring) - 1):
        a, b = ring[index], ring[index + 1]
        if _on_segment(a, b, point):
            return True
        if (a[1] > lat) != (b[1] > lat):
            crossing = (b[0] - a[0]) * (lat - a[1]) / (b[1] - a[1]) + a[0]
            if lng < crossing:
                inside = not inside
    return inside


def _ref(user_id: str, area_id: int):
    from firebase_admin import firestore
    return firestore.client().collection("users").document(user_id).collection("farm_areas").document(str(area_id))


def _present(data: dict | None, area_id: int) -> dict:
    data = dict(data or {})
    used = max(0, min(2, int(data.get("changes_used") or 0)))
    return {"area_id": area_id, "farm_name": data.get("farm_name") or f"Area {area_id}",
            "center_latitude": data.get("center_latitude"), "center_longitude": data.get("center_longitude"),
            "boundary_geojson": data.get("boundary_geojson"),
            "initial_boundary_confirmed": bool(data.get("initial_boundary_confirmed")),
            "changes_used": used, "changes_remaining": max(0, 2 - used),
            "boundary_locked": used >= 2, "updated_at": data.get("updated_at")}


def list_boundaries(user_id: str) -> list[dict]:
    docs = {int(doc.id): doc.to_dict() or {} for doc in _ref(user_id, 1).parent.stream() if doc.id.isdigit() and int(doc.id) in AREA_IDS}
    return [_present(docs.get(area_id), area_id) for area_id in sorted(AREA_IDS)]


def save_boundary(user_id: str, area_id: int, farm_name: object, center_latitude: object,
                  center_longitude: object, boundary_geojson: object) -> dict:
    if area_id not in AREA_IDS:
        raise BoundaryError("Area must be between 1 and 4")
    polygon = normalize_polygon(boundary_geojson)
    lat, lng = _finite(center_latitude, -90, 90), _finite(center_longitude, -180, 180)
    name = str(farm_name or f"Area {area_id}").strip()[:100] or f"Area {area_id}"
    ref = _ref(user_id, area_id)
    from firebase_admin import firestore

    @firestore.transactional
    def commit(transaction):
        snapshot = ref.get(transaction=transaction)
        old = snapshot.to_dict() if snapshot.exists else {}
        used = max(0, int(old.get("changes_used") or 0))
        initial = bool(old.get("initial_boundary_confirmed"))
        if initial and used >= 2:
            raise BoundaryError("This farm area has reached its maximum of 2 confirmed changes. Its boundary is now locked.")
        next_used = used + 1 if initial else 0
        value = {"owner_uid": user_id, "area_id": area_id, "farm_name": name,
                 "center_latitude": lat, "center_longitude": lng, "boundary_geojson": polygon,
                 "initial_boundary_confirmed": True, "changes_used": next_used,
                 "updated_at": _now()}
        if not initial:
            value["created_at"] = _now()
        transaction.set(ref, value, merge=True)
        return _present({**old, **value}, area_id)

    return commit(firestore.client().transaction())


def delete_boundary(user_id: str, area_id: int) -> dict:
    if area_id not in AREA_IDS:
        raise BoundaryError("Area must be between 1 and 4")
    ref = _ref(user_id, area_id)
    from firebase_admin import firestore

    @firestore.transactional
    def commit(transaction):
        snapshot = ref.get(transaction=transaction)
        old = snapshot.to_dict() if snapshot.exists else None
        if not old or not old.get("initial_boundary_confirmed") or not old.get("boundary_geojson"):
            raise BoundaryError("No confirmed boundary exists for this area")
        used = max(0, int(old.get("changes_used") or 0))
        if used >= 2:
            raise BoundaryError("This farm area has reached its maximum of 2 confirmed changes. Its boundary is now locked.")
        value = {"boundary_geojson": None, "changes_used": used + 1, "updated_at": _now()}
        transaction.update(ref, value)
        return _present({**old, **value}, area_id)

    return commit(firestore.client().transaction())


def match_area(boundaries: list[dict], latitude: object, longitude: object) -> tuple[int | None, bool]:
    matches = [int(item["area_id"]) for item in boundaries if item.get("boundary_geojson")
               and point_in_polygon(latitude, longitude, item["boundary_geojson"])]
    return (matches[0], False) if len(matches) == 1 else (None, len(matches) > 1)


def reassociate_existing_detections(user_id: str) -> int:
    """Refresh lightweight area metadata from original GPS; never reruns inference."""
    from firebase_admin import firestore
    db = firestore.client()
    areas = list_boundaries(user_id)
    changed = 0
    batch = db.batch()
    pending = 0
    for document in db.collection("users").document(user_id).collection("detections").stream():
        record = document.to_dict() or {}
        gps = record.get("gps_data") or {}
        if not isinstance(gps, dict):
            continue
        area_id, conflict = match_area(areas, gps.get("latitude"), gps.get("longitude"))
        old_id, old_conflict = record.get("farm_area_id"), bool(record.get("farm_area_conflict"))
        if old_id == area_id and old_conflict == conflict:
            continue
        update = {"farm_area_id": area_id, "farm_area_conflict": conflict, "farm_area_updated_at": _now()}
        batch.update(document.reference, update)
        changed += 1; pending += 1
        if pending == 450:
            batch.commit(); batch = db.batch(); pending = 0
    if pending:
        batch.commit()
    return changed
