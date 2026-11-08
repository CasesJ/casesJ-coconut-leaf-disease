"""Offline worker protocol and source-integrity tests; no model/provider calls."""
import asyncio
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import numpy as np
from fastapi import HTTPException
from starlette.requests import Request

from app.services import hybrid_source
from app.services.hybrid_source import SourceIntegrityError
from app.infrastructure import cloudinary_storage
from app.services.hybrid_worker_auth import canonical_worker_message, sign_worker_request


def _run_immediate(coroutine):
    """Drive an immediately completing request guard without a Windows event loop."""
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    raise AssertionError("Request guard unexpectedly waited for I/O")


def _asset():
    return {"public_id":"neuralcoco/hybrid-original/" + "a"*32 + ".jpg",
            "resource_type":"raw", "delivery_type":"authenticated", "format":"jpg"}


class Response:
    def __init__(self, chunks): self.chunks=chunks
    def raise_for_status(self): pass
    def iter_content(self, chunk_size): return iter(self.chunks)
    def close(self): pass


def _workspace_temp():
    path=Path("tmp") / f"hybrid-worker-test-{uuid4().hex}"
    path.mkdir(parents=True)
    return path


def test_worker_signatures_cover_method_path_nonce_timestamp_and_body():
    message=canonical_worker_message("POST", "/hybrid/worker/claim", 10, "n", b'{"a":1}')
    assert b"POST\n/hybrid/worker/claim\n10\nn\n" in message
    assert sign_worker_request("secret", "POST", "/hybrid/worker/claim", 10, "n", b"{}") != sign_worker_request("secret", "POST", "/hybrid/worker/claim", 10, "other", b"{}")


def test_worker_timing_log_is_bounded_and_contains_no_payload_data(caplog):
    from hybrid_worker import _log_timing

    with caplog.at_level("INFO", logger="coconutai.hybrid_worker"):
        _log_timing("a" * 32, "worker_pipeline", duration_ms=12.345, ignored=float("inf"))

    assert "job_id=" + "a" * 32 in caplog.text
    assert "duration_ms=12.3" in caplog.text
    assert "ignored" not in caplog.text
    assert "secret" not in caplog.text.lower()


def test_render_worker_memory_log_uses_existing_rss_and_cgroup_metrics(monkeypatch, caplog):
    from app.api import hybrid_worker as worker_api
    monkeypatch.setattr(worker_api, "process_memory_metrics", lambda: {
        "rss_bytes": 260 * 1024 * 1024,
        "cgroup_current_bytes": 290 * 1024 * 1024,
        "cgroup_limit_bytes": 512 * 1024 * 1024,
        "cgroup_oom_kill": 0,
    })
    with caplog.at_level("INFO", logger=worker_api.__name__):
        worker_api._log_render_memory("worker_reconcile")
    assert "stage=worker_reconcile rss_mib=260.0 cgroup_mib=290.0" in caplog.text
    assert "cgroup_limit_mib=512.0 oom_kill=0" in caplog.text
    assert "secret" not in caplog.text.lower()


def test_worker_completion_survives_timing_persistence_failure(monkeypatch, caplog):
    from app.api import hybrid_worker as worker_api

    class Store:
        db = object()
        completed = False
        def complete(self, *_args):
            self.completed = True
            return {"status": "completed"}
        def record_timing(self, *_args):
            raise RuntimeError("diagnostics unavailable")

    store=Store()
    async def authenticated(_request):
        return "worker", {"lease_token":"a"*32, "result":{}}
    async def inline(function, *args):
        return function(*args)

    monkeypatch.setattr(worker_api, "_authenticated_payload", authenticated)
    monkeypatch.setattr(worker_api, "_store", lambda: store)
    monkeypatch.setattr(worker_api, "run_in_threadpool", inline)
    monkeypatch.setattr(worker_api, "_log_render_memory", lambda _stage: None)
    monkeypatch.setattr("app.services.hybrid_record_commit.commit_completed_job", lambda *_args: {
        "record_id":"hybrid_record", "created":True,
    })

    with caplog.at_level("WARNING", logger=worker_api.__name__):
        response=asyncio.run(worker_api.complete("b"*32, object()))

    assert response == {"status":"completed", "record_id":"hybrid_record", "created":True}
    assert store.completed
    assert "stage=render_completion" in caplog.text
    assert "diagnostics unavailable" not in caplog.text


def test_worker_request_rejects_oversize_before_full_buffer_or_hmac(monkeypatch):
    from app.api import hybrid_worker as worker_api
    chunks=[b"x" * (worker_api._MAX_BODY - 1), b"yz", b"must-not-read"]
    reads=[]
    async def receive():
        value=chunks.pop(0)
        reads.append(len(value))
        return {"type":"http.request", "body":value, "more_body":bool(chunks)}
    request=Request({"type":"http", "method":"POST", "path":"/hybrid/worker/claim",
                     "headers":[]}, receive)
    async def forbidden_body(): raise AssertionError("request.body() must not be used")
    monkeypatch.setattr(request, "body", forbidden_body)
    with pytest.raises(HTTPException) as raised:
        _run_immediate(worker_api._authenticated_payload(request))
    assert raised.value.status_code == 413
    assert reads == [worker_api._MAX_BODY - 1, 2]


def test_worker_request_rejects_declared_oversize_without_reading_body():
    from app.api import hybrid_worker as worker_api
    async def receive(): raise AssertionError("oversized body must not be read")
    request=Request({"type":"http", "method":"POST", "path":"/hybrid/worker/claim",
                     "headers":[(b"content-length", str(worker_api._MAX_BODY + 1).encode())]}, receive)
    with pytest.raises(HTTPException) as raised:
        _run_immediate(worker_api._authenticated_payload(request))
    assert raised.value.status_code == 413


def test_streamed_source_is_verified_before_decode(monkeypatch):
    content=b"\xff\xd8\xff\xe0exact-original-bytes\xff\xd9"
    monkeypatch.setattr(hybrid_source, "_configure", lambda: ("cloud", "key", "secret"))
    monkeypatch.setattr("cloudinary.utils.private_download_url", lambda *a, **k: "https://api.cloudinary.com/v1_1/cloud/raw/download")
    monkeypatch.setattr(hybrid_source.requests, "get", lambda *a, **k: Response([content[:4], content[4:]]))
    directory=_workspace_temp()
    try:
        path=hybrid_source.download_verified_source(_asset(), hashlib.sha256(content).hexdigest(), len(content), directory)
        assert path.read_bytes() == content
    finally:
        shutil.rmtree(directory)


def test_admin_format_none_produces_worker_downloadable_jpeg_reference(monkeypatch):
    public_id = "neuralcoco/hybrid-original/" + "a" * 64 + ".jpg"
    content = b"\xff\xd8\xff\xe0original-jpeg-bytes\xff\xd9"
    monkeypatch.setattr(cloudinary_storage, "_configure", lambda: ("cloud", "key", "secret"))
    monkeypatch.setattr("cloudinary.api.resource", lambda *a, **k: {
        "public_id": public_id, "asset_id": "asset-1", "resource_type": "raw",
        "type": "authenticated", "bytes": len(content), "format": None, "version": 7,
        "context": {"custom": {"hybrid_job_id": "b" * 32, "hybrid_user_id": "farmer"}},
    })
    reference = cloudinary_storage.verify_hybrid_uploaded_asset(
        public_id=public_id, job_id="b" * 32, user_id="farmer",
        expected_bytes=len(content), asset_id="asset-1",
    )
    monkeypatch.setattr(hybrid_source, "_configure", lambda: ("cloud", "key", "secret"))
    requested = {}
    def private_url(_public_id, image_format, **kwargs):
        requested.update(public_id=_public_id, image_format=image_format, **kwargs)
        return "https://api.cloudinary.com/v1_1/cloud/raw/download"
    monkeypatch.setattr("cloudinary.utils.private_download_url", private_url)
    monkeypatch.setattr(hybrid_source.requests, "get", lambda *a, **k: Response([content]))
    directory = _workspace_temp()
    try:
        path = hybrid_source.download_verified_source(
            reference, hashlib.sha256(content).hexdigest(), len(content), directory,
        )
        assert path.read_bytes() == content
        assert requested["public_id"] == public_id
        assert requested["image_format"] == "jpg"
        assert requested["resource_type"] == "raw"
        assert requested["type"] == "authenticated"
    finally:
        shutil.rmtree(directory)


def test_streamed_source_hash_mismatch_removes_temp_file(monkeypatch):
    content=b"untrusted"
    monkeypatch.setattr(hybrid_source, "_configure", lambda: ("cloud", "key", "secret"))
    monkeypatch.setattr("cloudinary.utils.private_download_url", lambda *a, **k: "https://api.cloudinary.com/v1_1/cloud/raw/download")
    monkeypatch.setattr(hybrid_source.requests, "get", lambda *a, **k: Response([content]))
    directory=_workspace_temp()
    try:
        with pytest.raises(SourceIntegrityError):
            hybrid_source.download_verified_source(_asset(), "0"*64, len(content), directory)
        assert list(directory.iterdir()) == []
    finally:
        shutil.rmtree(directory)


def test_matching_hash_does_not_authorize_non_jpeg_bytes(monkeypatch):
    content = b"\x89PNG\r\n\x1a\nrenamed-as-jpeg"
    monkeypatch.setattr(hybrid_source, "_configure", lambda: ("cloud", "key", "secret"))
    monkeypatch.setattr("cloudinary.utils.private_download_url", lambda *a, **k:
                        "https://api.cloudinary.com/v1_1/cloud/raw/download")
    monkeypatch.setattr(hybrid_source.requests, "get", lambda *a, **k: Response([content]))
    directory = _workspace_temp()
    try:
        with pytest.raises(SourceIntegrityError, match="not a JPEG/MPO"):
            hybrid_source.download_verified_source(
                _asset(), hashlib.sha256(content).hexdigest(), len(content), directory,
            )
        assert list(directory.iterdir()) == []
    finally:
        shutil.rmtree(directory)


def test_local_thumbnail_is_bounded_and_uses_existing_inference_pixels():
    from hybrid_worker import _encode_hybrid_thumbnail
    image = np.zeros((2400, 4800, 3), dtype=np.uint8)
    encoded = _encode_hybrid_thumbnail(image)
    assert encoded[:2] == b"\xff\xd8"
    # A JPEG thumbnail must be materially smaller than the already-local BGR
    # inference raster; Render never receives this raster.
    assert len(encoded) < image.nbytes // 100


def test_worker_cli_does_not_require_web_expert_credentials():
    env={key:value for key,value in os.environ.items()
         if key not in {"EXPERT_ACCOUNT_EMAIL", "EXPERT_ACCOUNT_PASSWORD",
                        "FIREBASE_SERVICE_ACCOUNT_JSON", "GOOGLE_APPLICATION_CREDENTIALS"}}
    env["COCONUTAI_WORKER_PROCESS"]="1"
    result=subprocess.run(
        [sys.executable, "hybrid_worker.py", "--help"], cwd=Path(__file__).parents[1],
        env=env, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0
    assert "outbound hybrid inference worker" in result.stdout
