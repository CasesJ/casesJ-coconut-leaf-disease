"""Current CoconutAI disease-label policy and legacy record aliases."""
from __future__ import annotations

GREY_LEAF_SPOT = "Grey Leaf Spot"
_LEGACY_APPLICATION_ALIASES = {"pestalotiopsis": GREY_LEAF_SPOT}


def normalize_current_disease_label(value: object) -> str:
    """Return the current application label without rewriting stored records.

    Pestalotiopsis is retained solely as a historic CoconutAI record alias.  It
    is not a statement that the two disease names are biologically equivalent.
    """
    label = str(value or "").strip().replace("_", " ")
    if label.lower() == "bud root":
        return "Bud Rot"
    if label.lower() == "grey leaf spot":
        return GREY_LEAF_SPOT
    return _LEGACY_APPLICATION_ALIASES.get(label.lower(), label)
