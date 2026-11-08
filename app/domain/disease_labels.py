"""Canonical disease labels shared by cloud inference and recommendations."""
from __future__ import annotations

import re


_CANONICAL_LABELS = {
    "caterpillars": "Caterpillars",
    "drying of leaflets": "Drying of Leaflets",
    "healthy": "Healthy",
    "pestalotiopsis": "Pestalotiopsis",
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
