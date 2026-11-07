"""Offline worker protocol and source-integrity tests; no model/provider calls."""
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
    content=b"exact-original-bytes"
    monkeypatch.setattr(hybrid_source, "_configure", lambda: ("cloud", "key", "secret"))
    monkeypatch.setattr("cloudinary.utils.private_download_url", lambda *a, **k: "https://api.cloudinary.com/v1_1/cloud/raw/download")
    monkeypatch.setattr(hybrid_source.requests, "get", lambda *a, **k: Response([content[:4], content[4:]]))
    directory=_workspace_temp()
    try:
        path=hybrid_source.download_verified_source(_asset(), hashlib.sha256(content).hexdigest(), len(content), directory)
        assert path.read_bytes() == content
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
