from app.services.farm_boundary_service import BoundaryError, match_area, normalize_polygon, point_in_polygon


SQUARE = {"type": "Polygon", "coordinates": [[[125.0, 7.0], [125.1, 7.0], [125.1, 7.1], [125.0, 7.1], [125.0, 7.0]]]}


def test_polygon_is_closed_and_accepts_boundary_points():
    polygon = normalize_polygon({"type": "Polygon", "coordinates": [SQUARE["coordinates"][0][:-1]]})
    assert polygon["coordinates"][0][0] == polygon["coordinates"][0][-1]
    assert point_in_polygon(7.05, 125.05, polygon)
    assert point_in_polygon(7.0, 125.05, polygon)


def test_invalid_self_intersection_is_rejected():
    bow_tie = {"type": "Polygon", "coordinates": [[[125, 7], [125.1, 7.1], [125.1, 7], [125, 7.1], [125, 7]]]}
    try:
        normalize_polygon(bow_tie)
    except BoundaryError:
        pass
    else:
        raise AssertionError("self-intersecting polygon was accepted")


def test_overlaps_are_not_assigned_arbitrarily():
    boundaries = [{"area_id": 1, "boundary_geojson": SQUARE}, {"area_id": 2, "boundary_geojson": SQUARE}]
    assert match_area(boundaries, 7.05, 125.05) == (None, True)
    assert match_area(boundaries[:1], 8, 126) == (None, False)
