"""Regression checks for the intentionally disabled ML recommendation path."""

import ast
from pathlib import Path


MODEL_PATH = Path(__file__).resolve().parents[1] / "model.py"


def test_startup_does_not_import_or_deserialize_disabled_recommendation_pickles():
    """Keep disabled sklearn artifacts out of the runtime startup path."""
    source = MODEL_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert "import pickle" not in source
    assert ".pkl" not in source
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "load"
        for node in ast.walk(tree)
    )


def test_hardcoded_recommendation_branch_remains_intentionally_disabled():
    """Guard against accidentally activating archived ML recommendations."""
    source = MODEL_PATH.read_text(encoding="utf-8")
    assert "if False and self.ml_models_loaded and self.treatment_clf is not None:" in source
