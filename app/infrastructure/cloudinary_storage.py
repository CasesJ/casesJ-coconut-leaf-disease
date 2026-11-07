"""Private Cloudinary storage for new evidence, with no client-side secrets."""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from concurrent.futures import Future
from collections import OrderedDict
from io import BytesIO
from urllib.parse import urlsplit
from uuid import uuid4

import requests

from app.core import config

logger = logging.getLogger(__name__)

_CONFIG_LOCK = threading.Lock()
_CONFIGURED_VALUES: tuple[str, str, str] | None = None
_DOWNLOAD_LOCK = threading.Lock()
_INFLIGHT_DOWNLOADS: dict[tuple, Future[tuple[bytes, str]]] = {}
_DOWNLOAD_CACHE = OrderedDict()
_DOWNLOAD_CACHE_BYTES = 32 * 1024 * 1024
_DOWNLOAD_CACHE_TTL = 60
_DOWNLOAD_MAX_ATTEMPTS = 3
_DOWNLOAD_RETRY_BACKOFF_SECONDS = (0.2, 0.5)
_RETRYABLE_DOWNLOAD_STATUSES = {408, 500, 502, 503, 504}
_DOWNLOAD_URL_EXPIRY_MARGIN_SECONDS = 30
_PUBLIC_ID_PATTERN = re.compile(r"^neuralcoco/[a-z0-9_-]+/[a-f0-9]{32}$")
_HYBRID_SOURCE_PUBLIC_ID_PATTERN = re.compile(
    r"^neuralcoco/hybrid-original/(?:[a-f0-9]{32}|[a-f0-9]{64})\.jpg$"
)
_ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png"}
_URL_PATTERN = re.compile(r"(?i)\b(?:https?://|cloudinary://)\S+")
_SENSITIVE_PARAMETER_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|api[_-]?secret|authorization|signature|token|"
    r"private[_-]?(?:download[_-]?)?url)\b(\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)


class CloudinaryStorageError(RuntimeError):
    """A safe-to-report error without provider URLs or credentials."""


def create_hybrid_upload_signature(*, public_id: str, job_id: str, user_id: str,
                                   timestamp: int) -> dict:
    """Create a narrow signed direct-upload capability for one hybrid intent.

    The API secret stays here.  The browser receives only an API key and a
    signature covering the exact authenticated public ID and ownership context.
    """
    if (not _HYBRID_SOURCE_PUBLIC_ID_PATTERN.fullmatch(public_id)
            or not re.fullmatch(r"[a-f0-9]{32}", job_id)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", user_id)
            or not isinstance(timestamp, int)):
        raise CloudinaryStorageError("Invalid hybrid upload authorization")
    cloud_name, api_key, api_secret = _configure()
    context = f"hybrid_job_id={job_id}|hybrid_user_id={user_id}"
    params = {
        "public_id": public_id,
        "timestamp": timestamp,
        "type": "authenticated",
        # Raw delivery is deliberate: Cloudinary stores the uploaded DJI MPO/
        # JPEG byte stream without image decoding, recompression, or metadata
        # stripping. The worker will later create normal evidence derivatives.
        "context": context,
        "overwrite": "false",
        "unique_filename": "false",
        "use_filename": "false",
        "allowed_formats": "jpg,jpeg",
    }
    try:
        from cloudinary import utils  # noqa: PLC0415
        signature = utils.api_sign_request(params, api_secret)
    except Exception as error:
        _log_cloudinary_failure("hybrid upload signature", error)
        raise CloudinaryStorageError("Cloudinary upload authorization is unavailable") from None
    return {
        "cloud_name": cloud_name,
        "api_key": api_key,
        "signature": signature,
        **params,
    }


def verify_hybrid_uploaded_asset(*, public_id: str, job_id: str, user_id: str,
                                 expected_bytes: int, asset_id: str) -> dict:
    """Verify browser-reported upload metadata through Cloudinary's Admin API.

    This intentionally never downloads, decodes, or transforms the original
    image. Cloudinary cannot report a SHA-256, so hash verification is recorded
    as pending for the local inference worker rather than being falsely claimed
    at upload confirmation time.
    """
    if (not _HYBRID_SOURCE_PUBLIC_ID_PATTERN.fullmatch(public_id)
            or not re.fullmatch(r"[a-f0-9]{32}", job_id)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", user_id)
            or not isinstance(expected_bytes, int) or expected_bytes < 1
            or not isinstance(asset_id, str) or not asset_id):
        raise CloudinaryStorageError("Invalid hybrid upload confirmation")
    try:
        _configure()
        from cloudinary import api  # noqa: PLC0415
        resource = api.resource(
            public_id, resource_type="raw", type="authenticated", context=True,
        )
    except Exception as error:
        _log_cloudinary_failure("hybrid upload verification", error)
        raise CloudinaryStorageError("Cloudinary upload could not be verified") from None

    context = resource.get("context") if isinstance(resource, dict) else None
    custom_context = context.get("custom") if isinstance(context, dict) else None
    expected_context = {"hybrid_job_id": job_id, "hybrid_user_id": user_id}
    actual_context = custom_context if isinstance(custom_context, dict) else {}
    image_format = str(resource.get("format") or "").lower() if isinstance(resource, dict) else ""
    if (not isinstance(resource, dict)
            or resource.get("public_id") != public_id
            or resource.get("asset_id") != asset_id
            or resource.get("resource_type") != "raw"
            or resource.get("type") != "authenticated"
            or int(resource.get("bytes") or -1) != expected_bytes
            or image_format not in {"jpg", "jpeg"}
            or actual_context != expected_context):
        raise CloudinaryStorageError("Cloudinary upload verification failed")
    version = resource.get("version")
    try:
        version = int(version)
    except (TypeError, ValueError):
        raise CloudinaryStorageError("Cloudinary upload verification failed") from None
    return {
        "provider": "cloudinary", "asset_id": asset_id, "public_id": public_id,
        "resource_type": "raw", "delivery_type": "authenticated",
        "version": version, "format": image_format,
        "content_type": "image/jpeg", "byte_size": expected_bytes,
        "ownership_context": expected_context,
    }


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
                        timeout=config.CLOUDINARY_UPLOAD_TIMEOUT_SECONDS,
                    )
                    # Cloudinary 1.46 creates its uploader PoolManager at module
                    # import with urllib3's default maxsize=1. Replace that one
                    # process-wide connector with a bounded reusable pool.
                    from cloudinary import uploader, utils  # noqa: PLC0415
                    uploader._http = utils.get_http_connector(  # noqa: SLF001
                        cloudinary.config(),
                        {**cloudinary.CERT_KWARGS,
                         "num_pools": 4,
                         "maxsize": config.CLOUDINARY_POOL_MAXSIZE,
                         "block": True},
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
    resource_type = reference.get("resource_type")
    normal_image = (isinstance(public_id, str) and _PUBLIC_ID_PATTERN.fullmatch(public_id)
                    and resource_type == "image")
    hybrid_source = (isinstance(public_id, str) and _HYBRID_SOURCE_PUBLIC_ID_PATTERN.fullmatch(public_id)
                     and resource_type == "raw")
    if (not (normal_image or hybrid_source)
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


def _upload_error_category(error: object = None, provider_error: object = None) -> str:
    text = f"{type(error).__name__} {error} {provider_error}".lower()
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if any(value in text for value in ("connection", "protocolerror", "socket", "reset", "dns")):
        return "connection"
    if provider_error is not None or _http_status(error, provider_error) is not None:
        return "api_rejection"
    return "unexpected"


def upload_asset(content: bytes, content_type: str, category: str, *,
                 operation_id: str = "unknown", source_bytes: int | None = None) -> dict:
    """Upload bytes as an authenticated asset and return a durable Firestore ref."""
    if not isinstance(content, bytes) or not content:
        raise CloudinaryStorageError("Image content is empty")
    if len(content) > config.MAX_IMAGE_UPLOAD_BYTES:
        raise CloudinaryStorageError("Image content exceeds the configured limit")
    if content_type not in _ALLOWED_CONTENT_TYPES:
        raise CloudinaryStorageError("Unsupported image content type")
    if not isinstance(category, str) or not re.fullmatch(r"[a-z0-9_-]{1,40}", category):
        raise CloudinaryStorageError("Invalid image category")

    upload_started = time.perf_counter()
    logger.info(
        "Cloudinary upload start operation_id=%s source_bytes=%d stored_bytes=%d image_type=%s category=%s",
        operation_id, source_bytes if source_bytes is not None else len(content),
        len(content), content_type, category,
    )
    try:
        _configure()
        from cloudinary import uploader  # noqa: PLC0415
    except CloudinaryStorageError:
        logger.warning(
            "Cloudinary upload end operation_id=%s duration_ms=%.0f source_bytes=%d stored_bytes=%d status=failed error_category=configuration",
            operation_id, (time.perf_counter() - upload_started) * 1000,
            source_bytes if source_bytes is not None else len(content), len(content),
        )
        raise
    except Exception as error:
        logger.warning(
            "Cloudinary upload end operation_id=%s duration_ms=%.0f source_bytes=%d stored_bytes=%d status=failed error_category=configuration",
            operation_id, (time.perf_counter() - upload_started) * 1000,
            source_bytes if source_bytes is not None else len(content), len(content),
        )
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
            timeout=config.CLOUDINARY_UPLOAD_TIMEOUT_SECONDS,
        )
        if isinstance(result, dict) and isinstance(result.get("error"), dict):
            duration_ms = (time.perf_counter() - upload_started) * 1000
            logger.warning(
                "Cloudinary upload end operation_id=%s duration_ms=%.0f source_bytes=%d stored_bytes=%d status=failed error_category=%s",
                operation_id, duration_ms, source_bytes if source_bytes is not None else len(content),
                len(content), _upload_error_category(provider_error=result["error"]),
            )
            _log_cloudinary_failure("upload", provider_error=result["error"])
            _destroy_public_id(public_id)
            raise CloudinaryStorageError("Cloudinary image upload failed") from None
        if (not isinstance(result, dict) or result.get("type") != "authenticated"
                or result.get("resource_type") != "image"
                or result.get("public_id") != public_id
                or not result.get("asset_id") or not result.get("version")
                or result.get("format") not in expected_formats):
            logger.warning(
                "Cloudinary upload end operation_id=%s duration_ms=%.0f source_bytes=%d stored_bytes=%d status=failed error_category=api_rejection",
                operation_id, (time.perf_counter() - upload_started) * 1000,
                source_bytes if source_bytes is not None else len(content), len(content),
            )
            _log_cloudinary_failure(
                "upload", message="Cloudinary returned incomplete asset metadata",
            )
            _destroy_public_id(public_id)
            raise CloudinaryStorageError("Cloudinary returned incomplete asset metadata")
        reference = {
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
        logger.info(
            "Cloudinary upload end operation_id=%s duration_ms=%.0f source_bytes=%d stored_bytes=%d status=success",
            operation_id, (time.perf_counter() - upload_started) * 1000,
            source_bytes if source_bytes is not None else len(content), len(content),
        )
        return reference
    except CloudinaryStorageError:
        raise
    except Exception as error:
        # The request may have reached Cloudinary before timing out. This ID is
        # unique to this attempt, so exact-ID compensation cannot hit history.
        duration_ms = (time.perf_counter() - upload_started) * 1000
        logger.warning(
            "Cloudinary upload end operation_id=%s duration_ms=%.0f source_bytes=%d stored_bytes=%d status=failed error_category=%s",
            operation_id, duration_ms, source_bytes if source_bytes is not None else len(content),
            len(content), _upload_error_category(error=error),
        )
        _destroy_public_id(public_id)
        _log_cloudinary_failure("upload", error)
        raise CloudinaryStorageError("Cloudinary image upload failed") from None


def upload_hybrid_thumbnail(content: bytes, job_id: str) -> dict:
    """Store one stable, authenticated preview for a hybrid job.

    Cloudinary and Firestore cannot commit atomically.  A deterministic public
    ID and a signed digest in Cloudinary context let retries adopt the same
    derivative after an ambiguous response, without another upload or any
    automatic deletion.
    """
    if (not isinstance(content, bytes) or not content or len(content) > 1_000_000
            or not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id)):
        raise CloudinaryStorageError("Hybrid thumbnail is invalid")
    public_id = f"neuralcoco/detection-thumbnail/{job_id}"
    digest = hashlib.sha256(content).hexdigest()
    context = f"hybrid_job_id={job_id}|thumbnail_sha256={digest}"
    try:
        _configure()
        from cloudinary import api, uploader  # noqa: PLC0415
    except Exception as error:
        _log_cloudinary_failure("hybrid thumbnail setup", error)
        raise CloudinaryStorageError("Hybrid thumbnail storage is unavailable") from None

    def verified_reference(resource):
        if not isinstance(resource, dict):
            return None
        custom = (resource.get("context") or {}).get("custom") or {}
        if (resource.get("public_id") != public_id
                or resource.get("resource_type") != "image"
                or resource.get("type") != "authenticated"
                or resource.get("format") not in {"jpg", "jpeg"}
                or resource.get("bytes") != len(content)
                or custom != {"hybrid_job_id": job_id, "thumbnail_sha256": digest}
                or not resource.get("asset_id") or not resource.get("version")):
            raise CloudinaryStorageError("Hybrid thumbnail identity conflict")
        return {
            "provider": "cloudinary", "asset_id": str(resource["asset_id"]),
            "public_id": public_id, "resource_type": "image",
            "delivery_type": "authenticated", "version": int(resource["version"]),
            "format": str(resource["format"]), "content_type": "image/jpeg",
            "byte_size": len(content), "sha256": digest,
        }

    def inspect_existing():
        try:
            return api.resource(public_id, resource_type="image", type="authenticated", context=True)
        except Exception as error:
            # Only a provider-confirmed 404 means there is no asset to adopt.
            if _http_status(error) == 404:
                return None
            raise CloudinaryStorageError("Hybrid thumbnail inspection is unavailable") from None

    existing = inspect_existing()
    if existing is not None:
        return verified_reference(existing)
    try:
        uploader.upload(
            BytesIO(content), resource_type="image", type="authenticated",
            public_id=public_id, overwrite=False, unique_filename=False,
            use_filename=False, context=context,
            timeout=config.CLOUDINARY_UPLOAD_TIMEOUT_SECONDS,
        )
    except Exception:
        # Upload may have succeeded despite a lost response.  An exact-ID
        # inspection can adopt it; otherwise leave the job retryable.
        pass
    existing = inspect_existing()
    if existing is None:
        raise CloudinaryStorageError("Hybrid thumbnail could not be verified")
    return verified_reference(existing)


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
            public_id, image_format,
            resource_type=str(reference.get("resource_type") or "image"), type="authenticated",
            expires_at=(int(time.time()) + max(
                60,
                _DOWNLOAD_MAX_ATTEMPTS * config.CLOUDINARY_READ_TIMEOUT_SECONDS
                + int(sum(_DOWNLOAD_RETRY_BACKOFF_SECONDS))
                + _DOWNLOAD_URL_EXPIRY_MARGIN_SECONDS,
            )),
        )
        parsed = urlsplit(url)
        resource_type = str(reference.get("resource_type") or "image")
        expected_prefix = f"/v1_1/{cloud_name}/{resource_type}/download"
        if parsed.scheme != "https" or parsed.hostname != "api.cloudinary.com" or parsed.path != expected_prefix:
            raise CloudinaryStorageError("Cloudinary generated an invalid download URL")
        content = _download_private_url(url)
        return content, str(reference.get("content_type") or "image/jpeg")
    except CloudinaryStorageError:
        raise
    except Exception as error:
        _log_cloudinary_failure("private download", error)
        raise CloudinaryStorageError("Cloudinary image is temporarily unavailable") from None


def stream_asset(reference: dict):
    """Yield an authenticated asset without buffering it in the Render process.

    This is used for hybrid raw originals only.  The first-party media route
    retains Firebase ownership checks, while the provider URL and its signature
    remain entirely server-side.
    """
    public_id, image_format = _validate_reference(reference)
    if reference.get("resource_type") != "raw":
        raise CloudinaryStorageError("Streaming is reserved for raw source assets")
    cloud_name, _, _ = _configure()
    try:
        from cloudinary import utils  # noqa: PLC0415
        url = utils.private_download_url(
            public_id, image_format, resource_type="raw", type="authenticated",
            expires_at=int(time.time()) + max(60, config.CLOUDINARY_READ_TIMEOUT_SECONDS + 30),
        )
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname != "api.cloudinary.com"
                or parsed.path != f"/v1_1/{cloud_name}/raw/download"):
            raise CloudinaryStorageError("Cloudinary generated an invalid download URL")
    except CloudinaryStorageError:
        raise
    except Exception as error:
        _log_cloudinary_failure("private stream", error)
        raise CloudinaryStorageError("Cloudinary image is temporarily unavailable") from None

    response = None
    try:
        for attempt in range(_DOWNLOAD_MAX_ATTEMPTS):
            response = requests.get(
                url, timeout=(5, config.CLOUDINARY_READ_TIMEOUT_SECONDS),
                stream=True, allow_redirects=False,
            )
            if response.status_code in _RETRYABLE_DOWNLOAD_STATUSES and attempt + 1 < _DOWNLOAD_MAX_ATTEMPTS:
                response.close()
                response = None
                time.sleep(_DOWNLOAD_RETRY_BACKOFF_SECONDS[attempt])
                continue
            response.raise_for_status()
            break
    except Exception as error:
        if response is not None:
            response.close()
        _log_cloudinary_failure("private stream", error)
        raise CloudinaryStorageError("Cloudinary image is temporarily unavailable") from None

    def iterator():
        total = 0
        try:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > config.MAX_IMAGE_UPLOAD_BYTES:
                    raise CloudinaryStorageError("Stored image exceeds the configured limit")
                yield chunk
            if not total:
                raise CloudinaryStorageError("Cloudinary returned an empty image")
        except Exception as error:
            _log_cloudinary_failure("private stream", error)
            # Once headers were sent, the client receives a truncated stream;
            # never substitute an untrusted source or expose provider details.
            raise CloudinaryStorageError("Cloudinary image stream was interrupted") from None
        finally:
            response.close()

    return iterator()


def download_asset(reference: dict) -> tuple[bytes, str]:
    """Download an authenticated asset through the backend only.

    Concurrent requests for the same asset share one provider download, including
    its bounded retry sequence, so bursts do not multiply Cloudinary traffic.
    """
    public_id, image_format = _validate_reference(reference)
    cloud_name, _, _ = _configure()
    key = (cloud_name, public_id, image_format, str(reference.get("version") or ""),
           str(reference.get("asset_id") or ""), str(reference.get("sha256") or ""))
    with _DOWNLOAD_LOCK:
        now = time.monotonic()
        for cached_key, (expires, _) in list(_DOWNLOAD_CACHE.items()):
            if expires <= now:
                del _DOWNLOAD_CACHE[cached_key]
        cached = _DOWNLOAD_CACHE.get(key)
        if cached:
            _DOWNLOAD_CACHE.move_to_end(key)
            return cached[1]
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
        with _DOWNLOAD_LOCK:
            if len(result[0]) <= _DOWNLOAD_CACHE_BYTES:
                _DOWNLOAD_CACHE[key] = (time.monotonic() + _DOWNLOAD_CACHE_TTL, result)
                while sum(len(entry[1][0]) for entry in _DOWNLOAD_CACHE.values()) > _DOWNLOAD_CACHE_BYTES:
                    _DOWNLOAD_CACHE.popitem(last=False)
        pending.set_result(result)
        return result
    except BaseException as error:
        pending.set_exception(error)
        raise
    finally:
        with _DOWNLOAD_LOCK:
            if _INFLIGHT_DOWNLOADS.get(key) is pending:
                del _INFLIGHT_DOWNLOADS[key]
