"""Streaming verification of authenticated raw hybrid source assets."""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

from app.infrastructure.cloudinary_storage import CloudinaryStorageError, _configure

_PUBLIC_ID = re.compile(r"^neuralcoco/hybrid-original/(?:[a-f0-9]{32}|[a-f0-9]{64})\.jpg$")


class HybridSourceError(RuntimeError):
    pass


class SourceIntegrityError(HybridSourceError):
    pass


def download_verified_source(asset: dict, expected_sha256: str, expected_bytes: int,
                             directory: Path | None = None) -> Path:
    """Stream one raw authenticated source to disk and verify it before decode."""
    public_id = asset.get("public_id") if isinstance(asset, dict) else None
    if (not isinstance(public_id, str) or not _PUBLIC_ID.fullmatch(public_id)
            or asset.get("resource_type") != "raw"
            or asset.get("delivery_type") != "authenticated"
            or asset.get("format") not in {"jpg", "jpeg"}
            or not isinstance(expected_sha256, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256)
            or not isinstance(expected_bytes, int) or expected_bytes < 1):
        raise SourceIntegrityError("Hybrid source reference is invalid")
    try:
        cloud_name, _, _ = _configure()
        from cloudinary import utils  # noqa: PLC0415
        url = utils.private_download_url(
            public_id, asset["format"], resource_type="raw", type="authenticated",
            expires_at=int(time.time()) + 120,
        )
    except CloudinaryStorageError as error:
        raise HybridSourceError("Cloudinary source access is unavailable") from error
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "api.cloudinary.com":
        raise SourceIntegrityError("Cloudinary generated an invalid source URL")
    directory = Path(directory) if directory else Path(tempfile.gettempdir())
    directory.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix="coconutai-hybrid-", suffix=".jpg", dir=directory, delete=False)
    path = Path(handle.name)
    digest, total = hashlib.sha256(), 0
    response = None
    succeeded = False
    try:
        response = requests.get(url, stream=True, timeout=(5, 30), allow_redirects=False)
        response.raise_for_status()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > expected_bytes:
                raise SourceIntegrityError("Hybrid source exceeds its expected size")
            digest.update(chunk)
            handle.write(chunk)
        handle.close()
        if total != expected_bytes or not hmac_compare(digest.hexdigest(), expected_sha256.lower()):
            raise SourceIntegrityError("Hybrid source hash or byte count does not match its upload intent")
        # Raw Cloudinary assets can report format=None. The signed public ID
        # alone is not proof that the original bytes are JPEG/MPO. The full
        # decoder still validates the image after this bounded header check.
        with path.open("rb") as source:
            if source.read(3) != b"\xff\xd8\xff":
                raise SourceIntegrityError("Hybrid source is not a JPEG/MPO image")
        succeeded = True
        return path
    except requests.RequestException as error:
        raise HybridSourceError("Cloudinary source download failed") from error
    finally:
        handle.close()
        if response is not None:
            response.close()
        if path.exists() and not succeeded:
            path.unlink(missing_ok=True)


def hmac_compare(left: str, right: str) -> bool:
    import hmac
    return hmac.compare_digest(left, right)
