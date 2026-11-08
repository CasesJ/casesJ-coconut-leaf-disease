"""Regression coverage for the lightweight Render readiness endpoint."""
import builtins

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import health
from app.core.config import INFERENCE_BACKEND


def test_health_does_not_import_or_initialize_model(monkeypatch):
    application = FastAPI()
    application.include_router(health.router)
    original_import = builtins.__import__

    def reject_model_import(name, *args, **kwargs):
        if name == "model":
            raise AssertionError("The health endpoint must not import the model")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_model_import)
    response = TestClient(application).get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "model": "YOLO26 v6",
        "backend": INFERENCE_BACKEND,
        "weights": "weights.xml" if INFERENCE_BACKEND == "openvino" else "weights.pt",
    }
