"""HMAC authentication shared by dormant Render worker routes and local client."""
from __future__ import annotations

import hashlib
import hmac
import time
from uuid import uuid4


def canonical_worker_message(method: str, path: str, timestamp: int, nonce: str,
                             body: bytes) -> bytes:
    digest = hashlib.sha256(body).hexdigest()
    return f"{method.upper()}\n{path}\n{timestamp}\n{nonce}\n{digest}".encode()


def sign_worker_request(secret: str, method: str, path: str, timestamp: int,
                        nonce: str, body: bytes) -> str:
    if not secret:
        raise ValueError("Worker secret is required")
    return hmac.new(
        secret.encode(), canonical_worker_message(method, path, timestamp, nonce, body),
        hashlib.sha256,
    ).hexdigest()


def make_worker_headers(secret: str, worker_id: str, method: str, path: str,
                        body: bytes) -> dict[str, str]:
    timestamp, nonce = int(time.time()), uuid4().hex
    return {
        "X-Hybrid-Worker-Id": worker_id,
        "X-Hybrid-Timestamp": str(timestamp),
        "X-Hybrid-Nonce": nonce,
        "X-Hybrid-Signature": sign_worker_request(secret, method, path, timestamp, nonce, body),
        "Content-Type": "application/json",
    }
