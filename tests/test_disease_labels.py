"""Regression tests for current labels and historical record compatibility."""
from app.domain.disease import GREY_LEAF_SPOT, normalize_current_disease_label


def test_grey_leaf_spot_is_the_current_application_label():
    assert normalize_current_disease_label("Grey Leaf Spot") == GREY_LEAF_SPOT
    assert normalize_current_disease_label("grey_leaf_spot") == GREY_LEAF_SPOT


def test_legacy_pestalotiopsis_is_displayed_compatibly_without_record_migration():
    assert normalize_current_disease_label("Pestalotiopsis") == GREY_LEAF_SPOT


def test_unrecognized_labels_are_not_misclassified_as_healthy():
    assert normalize_current_disease_label("New workflow label") == "New workflow label"
