"""Canonical disease labels shared by cloud inference and recommendations."""
from __future__ import annotations

import re


GREY_LEAF_SPOT = "Grey Leaf Spot"

# Pestalotiopsis is a historical CoconutAI record/model label.  Keep this
# application-level alias so existing records and expert overrides continue to
# work, while all newly parsed cloud detections use the current display label.
_CANONICAL_LABELS = {
    "caterpillars": "Caterpillars",
    "drying of leaflets": "Drying of Leaflets",
    "healthy": "Healthy",
    "pestalotiopsis": GREY_LEAF_SPOT,
    "grey leaf spot": GREY_LEAF_SPOT,
    "bud root": "Bud Rot",  # Known historical model spelling.
    "bud rot": "Bud Rot",
    "stem bleeding": "Stem Bleeding",
}


def canonical_disease_label(value: object) -> str | None:
    """Return the supported display label, or None for unsupported cloud output."""
    normalized = re.sub(r"[ _-]+", " ", str(value or "").strip().lower())
    return _CANONICAL_LABELS.get(normalized)


def canonical_disease_key(value: object) -> str | None:
    """Return a stable lower-case key for rule lookups and UI compatibility."""
    label = canonical_disease_label(value)
    return label.lower() if label else None
