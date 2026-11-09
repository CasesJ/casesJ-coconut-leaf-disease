"""Deployment blueprint regressions for the cloud-only Render path."""
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _variables() -> dict[str, object]:
    data = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    return {item["key"]: item for item in data["services"][0]["envVars"]}


def test_render_selects_roboflow_and_declares_required_configuration():
    variables = _variables()
    assert variables["INFERENCE_BACKEND"]["value"] == "roboflow"
    for key in ("ROBOFLOW_API_KEY", "ROBOFLOW_WORKSPACE", "ROBOFLOW_WORKFLOW_ID"):
        assert variables[key]["sync"] is False
    assert variables["ROBOFLOW_IMAGE_INPUT_KEY"]["value"] == "image"
    assert variables["ROBOFLOW_TIMEOUT_SECONDS"]["value"] == "45"
    assert "OPENVINO_MODEL_DIR" not in variables


def test_render_requirements_exclude_local_openvino_and_torch_runtimes():
    packages = {
        line.split("=", 1)[0].split(">", 1)[0].strip().lower()
        for line in (ROOT / "requirements-render.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert not {"openvino", "ultralytics", "torch"} & packages
