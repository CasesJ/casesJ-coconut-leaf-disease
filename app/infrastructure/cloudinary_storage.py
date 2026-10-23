"""Private Cloudinary storage for new evidence, with no client-side secrets."""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from concurrent.futures import Future
from io import BytesIO
from urllib.parse import urlsplit
from uuid import uuid4

import requests

from app.core import config

logger = logging.getLogger(__name__)

_CONFIG_LOCK = threading.Lock()
_CONFIGURED_VALUES: tuple[str, str, str] | None = None
_DOWNLOAD_LOCK = threading.Lock()
_INFLIGHT_DOWNLOADS: dict[tuple[str, str], Future[tuple[bytes, str]]] = {}
_DOWNLOAD_MAX_ATTEMPTS = 3
_DOWNLOAD_RETRY_BACKOFF_SECONDS = (0.2, 0.5)
_RETRYABLE_DOWNLOAD_STATUSES = {408, 500, 502, 503, 504}
_DOWNLOAD_URL_EXPIRY_MARGIN_SECONDS = 30
_PUBLIC_ID_PATTERN = re.compile(r"^neuralcoco/[a-z0-9_-]+/[a-f0-9]{32}$")
_ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png"}
_URL_PATTERN = re.compile(r"(?i)\b(?:https?://|cloudinary://)\S+")
_SENSITIVE_PARAMETER_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|api[_-]?secret|authorization|signature|token|"
    r"private[_-]?(?:download[_-]?)?url)\b(\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)


class CloudinaryStorageError(RuntimeError):
    """A safe-to-report error without provider URLs or credentials."""


def _safe_diagnostic_message(message: object) -> str:
    """Redact provider details before putting them in application logs."""
    safe_message = str(message)
    for secret in (
        config.CLOUDINARY_API_SECRET,
        config.CLOUDINARY_API_KEY,
    ):
        if secret:
            safe_message = safe_message.replace(secret, "[REDACTED]")
    safe_message = _URL_PATTERN.sub("[URL REDACTED]", safe_message)
    safe_message = _SENSITIVE_PARAMETER_PATTERN.sub(r"\1\2[REDACTED]", safe_message)
    return safe_message[:300]


def _http_status(error: object | None = None, provider_error: object | None = None) -> int | None:
    candidates = [
        provider_error.get("http_code") if isinstance(provider_error, dict) else None,
        getattr(error, "http_code", None),
        getattr(error, "status_code", None),
        getattr(getattr(error, "response", None), "status_code", None),
    ]
    for candidate in candidates:
        try:
            status = int(candidate)
        except (TypeError, ValueError):
            continue
        if 100 <= status <= 599:
            return status
    return None


def _log_cloudinary_failure(
    operation: str,
    error: Exception | None = None,
    *,
    provider_error: object | None = None,
    message: str | None = None,
) -> None:
    details = provider_error if isinstance(provider_error, dict) else {}
    error_type = type(error).__name__ if error is not None else "CloudinaryAPIError"
    detail_message = message or details.get("message") or (str(error) if error else "Provider returned an error")
    status = _http_status(error, provider_error)
    logger.warning(
        "Cloudinary %s failed error_type=%s http_status=%s message=%s",
        operation,
        error_type,
        status if status is not None else "unknown",
        _safe_diagnostic_message(detail_message),
    )


def _configure() -> tuple[str, str, str]:
    global _CONFIGURED_VALUES
    values = (
        config.CLOUDINARY_CLOUD_NAME,
        config.CLOUDINARY_API_KEY,
        config.CLOUDINARY_API_SECRET,
    )
    if not all(values):
        raise CloudinaryStorageError("Cloudinary storage is not configured")
    if values != _CONFIGURED_VALUES:
        with _CONFIG_LOCK:
            if values != _CONFIGURED_VALUES:
                try:
                    import cloudinary  # noqa: PLC0415
                    cloudinary.config(
                        cloud_name=values[0], api_key=values[1],
                        api_secret=values[2], secure=True,
                    )
                    _CONFIGURED_VALUES = values
                except Exception as error:
                    _log_cloudinary_failure("configuration", error)
                    raise CloudinaryStorageError("Cloudinary storage is unavailable") from None
    return values


def _validate_reference(reference: dict) -> tuple[str, str]:
    if not isinstance(reference, dict) or reference.get("provider") != "cloudinary":
        raise CloudinaryStorageError("Invalid Cloudinary asset reference")
    public_id = reference.get("public_id")
    if (not isinstance(public_id, str) or not _PUBLIC_ID_PATTERN.fullmatch(public_id)
            or reference.get("resource_type") != "image"
            or reference.get("delivery_type") != "authenticated"):
        raise CloudinaryStorageError("Invalid Cloudinary asset reference")
    image_format = reference.get("format")
    if image_format not in {"jpg", "jpeg", "png"}:
        raise CloudinaryStorageError("Unsupported Cloudinary image format")
    expected_content_type = "image/png" if image_format == "png" else "image/jpeg"
    if reference.get("content_type") != expected_content_type:
        raise CloudinaryStorageError("Invalid Cloudinary image content type")
    return public_id, image_format


def _destroy_public_id(public_id: str) -> bool:
    if not isinstance(public_id, str) or not _PUBLIC_ID_PATTERN.fullmatch(public_id):
        return False
    try:
        _configure()
        from cloudinary import uploader  # noqa: PLC0415
        result = uploader.destroy(
            public_id, resource_type="image", type="authenticated", invalidate=True,
        )
        succeeded = isinstance(result, dict) and result.get("result") in {"ok", "not found"}
        if not succeeded:
            _log_cloudinary_failure("cleanup", message="Cloudinary did not confirm asset cleanup")
        return succeeded
    except Exception as error:
        _log_cloudinary_failure("cleanup", error)
        return False


def upload_asset(content: bytes, content_type: str, category: str) -> dict:
    """Upload bytes as an authenticated asset and return a durable Firestore ref."""
    if not isinstance(content, bytes) or not content:
        raise CloudinaryStorageError("Image content is empty")
    if len(content) > config.MAX_IMAGE_UPLOAD_BYTES:
        raise CloudinaryStorageError("Image content exceeds the configured limit")
    if content_type not in _ALLOWED_CONTENT_TYPES:
        raise CloudinaryStorageError("Unsupported image content type")
    if not isinstance(category, str) or not re.fullmatch(r"[a-z0-9_-]{1,40}", category):
        raise CloudinaryStorageError("Invalid image category")

    try:
        _configure()
        from cloudinary import uploader  # noqa: PLC0415
    except CloudinaryStorageError:
        raise
    except Exception as error:
        _log_cloudinary_failure("SDK initialization", error)
        raise CloudinaryStorageError("Cloudinary storage is unavailable") from None

    public_id = f"neuralcoco/{category}/{uuid4().hex}"
    expected_formats = {"png"} if content_type == "image/png" else {"jpg", "jpeg"}
    try:
        result = uploader.upload(
            BytesIO(content),
            resource_type="image",
            type="authenticated",
            public_id=public_id,
            overwrite=False,
            unique_filename=False,
            use_filename=False,
            invalidate=True,
            return_error=True,
        )
        if isinstance(result, dict) and isinstance(result.get("error"), dict):
            _log_cloudinary_failure("upload", provider_error=result["error"])
            _destroy_public_id(public_id)
            raise CloudinaryStorageError("Cloudinary image upload failed") from None
        if (not isinstance(result, dict) or result.get("type") != "authenticated"
                or result.get("resource_type") != "image"
                or result.get("public_id") != public_id
                or not result.get("asset_id") or not result.get("version")
                or result.get("format") not in expected_formats):
            _log_cloudinary_failure(
                "upload", message="Cloudinary returned incomplete asset metadata",
            )
            _destroy_public_id(public_id)
            raise CloudinaryStorageError("Cloudinary returned incomplete asset metadata")
        return {
            "provider": "cloudinary",
            "asset_id": str(result["asset_id"]),
            "public_id": public_id,
            "resource_type": "image",
            "delivery_type": "authenticated",
            "version": int(result["version"]),
            "format": str(result["format"]),
            "content_type": content_type,
            "byte_size": int(result.get("bytes") or len(content)),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    except CloudinaryStorageError:
        raise
    except Exception as error:
        # The request may have reached Cloudinary before timing out. This ID is
        # unique to this attempt, so exact-ID compensation cannot hit history.
        _destroy_public_id(public_id)
        _log_cloudinary_failure("upload", error)
        raise CloudinaryStorageError("Cloudinary image upload failed") from None


def delete_asset(reference: dict) -> bool:
    """Delete one newly-created asset by its validated, unique public ID."""
    try:
        public_id, _ = _validate_reference(reference)
        return _destroy_public_id(public_id)
    except CloudinaryStorageError:
        return False


def _download_private_url(url: str) -> bytes:
    """Fetch bytes with bounded retries for transient provider/network failures."""
    for attempt in range(_DOWNLOAD_MAX_ATTEMPTS):
        response = None
        try:
            response = requests.get(
                url,
                timeout=(5, config.CLOUDINARY_READ_TIMEOUT_SECONDS),
                stream=True,
                allow_redirects=False,
            )
            status = getattr(response, "status_code", None)
            if status in _RETRYABLE_DOWNLOAD_STATUSES and attempt + 1 < _DOWNLOAD_MAX_ATTEMPTS:
                response.close()
                response = None
                time.sleep(_DOWNLOAD_RETRY_BACKOFF_SECONDS[attempt])
                continue

            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > config.MAX_IMAGE_UPLOAD_BYTES:
                    raise CloudinaryStorageError("Stored image exceeds the configured limit")
                chunks.append(chunk)
            content = b"".join(chunks)
            if not content:
                raise CloudinaryStorageError("Cloudinary returned an empty image")
            return content
        except (requests.Timeout, requests.ConnectionError) as error:
            if (isinstance(error, requests.exceptions.SSLError)
                    or attempt + 1 >= _DOWNLOAD_MAX_ATTEMPTS):
                raise
            if response is not None:
                response.close()
                response = None
            time.sleep(_DOWNLOAD_RETRY_BACKOFF_SECONDS[attempt])
        finally:
            if response is not None:
                response.close()
    raise AssertionError("Cloudinary download retry loop completed unexpectedly")


def _download_asset_once(reference: dict, public_id: str, image_format: str,
                         cloud_name: str) -> tuple[bytes, str]:
    try:
        from cloudinary import utils  # noqa: PLC0415
        url = utils.private_download_url(
            public_id, image_format, resource_type="image", type="authenticated",
            expires_at=(int(time.time()) + max(
                60,
                _DOWNLOAD_MAX_ATTEMPTS * config.CLOUDINARY_READ_TIMEOUT_SECONDS
                + int(sum(_DOWNLOAD_RETRY_BACKOFF_SECONDS))
                + _DOWNLOAD_URL_EXPIRY_MARGIN_SECONDS,
            )),
        )
        parsed = urlsplit(url)
        expected_prefix = f"/v1_1/{cloud_name}/image/download"
        if parsed.scheme != "https" or parsed.hostname != "api.cloudinary.com" or parsed.path != expected_prefix:
            raise CloudinaryStorageError("Cloudinary generated an invalid download URL")
        content = _download_private_url(url)
        return content, str(reference.get("content_type") or "image/jpeg")
    except CloudinaryStorageError:
        raise
    except Exception as error:
        _log_cloudinary_failure("private download", error)
        raise CloudinaryStorageError("Cloudinary image is temporarily unavailable") from None


def download_asset(reference: dict) -> tuple[bytes, str]:
    """Download an authenticated asset through the backend only.

    Concurrent requests for the same asset share one provider download, including
    its bounded retry sequence, so bursts do not multiply Cloudinary traffic.
    """
    public_id, image_format = _validate_reference(reference)
    cloud_name, _, _ = _configure()
    key = (public_id, image_format)
    with _DOWNLOAD_LOCK:
        pending = _INFLIGHT_DOWNLOADS.get(key)
        if pending is None:
            pending = Future()
            _INFLIGHT_DOWNLOADS[key] = pending
            is_leader = True
        else:
            is_leader = False

    if not is_leader:
        return pending.result()

    try:
        result = _download_asset_once(reference, public_id, image_format, cloud_name)
        pending.set_result(result)
        return result
    except BaseException as error:
        pending.set_exception(error)
        raise
    finally:
        with _DOWNLOAD_LOCK:
            if _INFLIGHT_DOWNLOADS.get(key) is pending:
                del _INFLIGHT_DOWNLOADS[key]
