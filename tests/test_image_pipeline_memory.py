"""Regression checks for the shared low-memory image pipeline budget."""
import asyncio
from types import SimpleNamespace

import pytest

from app.main import _is_memory_intensive_image_pipeline, _run_memory_intensive_image_pipeline


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


def test_failed_processing_releases_the_shared_slot_for_the_next_upload():
    async def exercise():
        slots = asyncio.Semaphore(1)

        async def fails(_request):
            raise RuntimeError("detector failed")

        with pytest.raises(RuntimeError, match="detector failed"):
            await _run_memory_intensive_image_pipeline(_request("POST", "/detect/image"), fails, slots=slots)

        completed = []

        async def succeeds(_request):
            completed.append(True)
            return "ok"

        assert await _run_memory_intensive_image_pipeline(_request("POST", "/detect/image"), succeeds, slots=slots) == "ok"
        assert completed == [True]

    asyncio.run(exercise())


def test_busy_pipeline_returns_actionable_503_without_running_processing():
    async def exercise():
        slots = asyncio.Semaphore(1)
        await slots.acquire()
        called = []

        async def should_not_run(_request):
            called.append(True)

        response = await _run_memory_intensive_image_pipeline(
            _request("POST", "/monitoring/cases/case-1/follow-ups"), should_not_run,
            slots=slots, queue_seconds=0.01,
        )
        assert response.status_code == 503
        assert response.headers["retry-after"] == "0"
        assert b"Image processing is busy" in response.body
        assert called == []

    asyncio.run(exercise())
