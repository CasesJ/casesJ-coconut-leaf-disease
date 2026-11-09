"""Regression coverage for the current Grey Leaf Spot application label."""
from app.domain.disease_labels import canonical_disease_key, canonical_disease_label
from app.domain import recommendation


def test_historical_pestalotiopsis_records_use_current_display_label_without_mutation():
    stored_record = {"detections": [{"class": "Pestalotiopsis", "confidence": 0.8}]}

    assert canonical_disease_label(stored_record["detections"][0]["class"]) == "Grey Leaf Spot"
    assert canonical_disease_key("Pestalotiopsis") == "grey leaf spot"
    assert stored_record["detections"][0]["class"] == "Pestalotiopsis"


def test_grey_leaf_spot_and_historical_alias_get_existing_leaf_spot_rules():
    current = recommendation.build_recommendation_response("Grey Leaf Spot", 0.8)
    historical = recommendation.build_recommendation_response("Pestalotiopsis", 0.8)

    assert current["disease"] == historical["disease"] == "Grey Leaf Spot"
    assert current["recommendations"] == historical["recommendations"]
    assert "Remove and properly dispose" in current["recommendations"]["treatment"]


def test_historical_expert_override_remains_available_to_current_grey_leaf_spot(monkeypatch):
    monkeypatch.setattr(recommendation, "load_expert_recommendations", lambda: {
        "pestalotiopsis": {"active": True, "treatment": "Historic expert treatment"},
    })
    base = recommendation.build_recommendation_response("Grey Leaf Spot", 0.8)

    result = recommendation.apply_expert_recommendation_override(base, "Grey Leaf Spot")

    assert result["source"] == "expert_override"
    assert result["recommendations"]["treatment"] == "Historic expert treatment"


def test_current_expert_recommendation_lookup_reads_historical_override_without_migration():
    overrides = {"pestalotiopsis": {"active": True, "treatment": "Historic expert treatment"}}

    assert recommendation.get_expert_recommendation_override(overrides, "Grey Leaf Spot") == overrides["pestalotiopsis"]
    assert overrides == {"pestalotiopsis": {"active": True, "treatment": "Historic expert treatment"}}
