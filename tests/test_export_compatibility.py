"""CSV output contract for historical and current detection representations."""
from copy import deepcopy

from app.services import export_service


def test_csv_keeps_legacy_json_fallbacks_coordinates_and_summary(monkeypatch):
    records = [
        {"detections": '[{"class":"Bud Rot","confidence":0.8,"severity":"high"},null]',
         "gps_data": '{"latitude":0,"longitude":125.6}', "lat": 7,
         "tree_id": "TREE-OLD", "timestamp": "2026-10-23", "email": "farmer@test"},
        {"inference_results": [{"disease_name": "Leaf Spot", "confidence": 0.6}],
         "gps_data": "broken-json", "lat": 7.1, "lng": 0, "severity": "medium"},
        {"detections": "{}", "gps_data": "[]"},
    ]
    original = deepcopy(records)
    limits = []

    def read(*, limit):
        limits.append(limit)
        return records

    monkeypatch.setattr(export_service, "get_all_user_records", read)
    assert export_service.generate_detections_csv() == (
        "Disease Name,Confidence,Severity,Field ID,Detection Date,Location,Email,Source\n"
        'Bud Rot,0.8000,high,TREE-OLD,2026-10-23,"0,125.6",farmer@test,detection\n'
        'Leaf Spot,0.6000,medium,,,"7.1,0",,detection\n'
        "\n=== INVENTORY SUMMARY ===\n\n"
        "Disease,Total Count,Avg Confidence,High,Medium,Low\n"
        "Bud Rot,1,0.8,1,0,0\n"
        "Leaf Spot,1,0.6,0,1,0\n"
        "\nTOTAL,2,0.7,1,1,0\n"
    )
    assert records == original
    assert limits == [2000]
