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
