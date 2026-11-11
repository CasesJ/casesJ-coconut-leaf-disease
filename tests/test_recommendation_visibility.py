from app.services.media_service import present_detection_record


def test_pending_farmer_record_does_not_expose_recommendation_snapshot():
    record = {
        "id": "record-1",
        "user_id": "farmer-1",
        "verification_status": "pending_verification",
        "recommendation_source": "farmer_review",
        "recommendation_snapshot": {
            "recommendation_scope": "record",
            "recommendations": {"treatment": "Do not expose this yet"},
        },
    }

    presented = present_detection_record(record)

    assert "recommendation_snapshot" not in presented
    assert "recommendation_source" not in presented


def test_verified_farmer_record_keeps_expert_recommendation_snapshot():
    snapshot = {
        "recommendation_scope": "record",
        "recommendations": {"treatment": "Expert-approved treatment"},
    }
    record = {
        "id": "record-1",
        "user_id": "farmer-1",
        "verification_status": "verified",
        "recommendation_source": "expert_override",
        "recommendation_snapshot": snapshot,
    }

    presented = present_detection_record(record)

    assert presented["recommendation_snapshot"] == snapshot
    assert presented["recommendation_source"] == "expert_override"
