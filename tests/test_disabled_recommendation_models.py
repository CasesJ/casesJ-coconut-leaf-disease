"""Regression checks for the supported hard-coded recommendation path."""

import ast
from pathlib import Path


MODEL_PATH = Path(__file__).resolve().parents[1] / "model.py"


def test_model_has_no_archived_ml_recommendation_implementation():
    """Keep deleted pickle-backed recommendation code from returning."""
    source = MODEL_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    for forbidden in ("pickle", ".pkl", "sklearn", "ml_models_loaded", "treatment_clf", "_create_features"):
        assert forbidden not in source
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "load"
        for node in ast.walk(tree)
    )


def test_hardcoded_recommendation_output_contract_remains_present():
    """Keep the current hard-coded disease lookup and response shape."""
    source = MODEL_PATH.read_text(encoding="utf-8")
    assert "'bud rot': {" in source
    assert "'model': 'Hardcoded'" in source
    assert "'treatment': rec['treatment']" in source
