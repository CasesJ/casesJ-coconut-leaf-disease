"""Regression checks for the shared low-memory image pipeline budget."""
from types import SimpleNamespace

from app.main import _is_memory_intensive_image_pipeline


def _request(method: str, path: str):
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path))


def test_all_server_image_inference_upload_routes_share_the_memory_budget():
    assert _is_memory_intensive_image_pipeline(_request("POST", "/detect/image"))
    assert _is_memory_intensive_image_pipeline(
        _request("POST", "/monitoring/cases/case-1/follow-ups")
    )


def test_non_upload_requests_do_not_consume_the_image_pipeline_budget():
    assert not _is_memory_intensive_image_pipeline(_request("GET", "/detect/image"))
    assert not _is_memory_intensive_image_pipeline(
        _request("POST", "/monitoring/cases/case-1")
    )


def test_cloud_mode_streaming_is_not_an_inference_path(monkeypatch):
    source = ("app/api/detections.py")
    content = open(source, encoding="utf-8").read()
    guard = content.index('if INFERENCE_BACKEND == "roboflow":', content.index("async def detect_stream"))
    detector_import = content.index("from model import detector", guard)
    assert guard < detector_import
    assert "Live streaming is disabled for cloud inference" in content[guard:detector_import]
