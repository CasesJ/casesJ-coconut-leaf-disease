"""Offline Cloudinary adapter and partial-upload regression tests."""
from concurrent.futures import ThreadPoolExecutor
import threading
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import cloudinary
import numpy as np
from PIL import Image
import pytest
import requests
from cloudinary.exceptions import Error as CloudinaryError

from app.infrastructure import cloudinary_storage as store
from app.infrastructure.imaging import image_utils


@pytest.fixture
def cloudinary_config(monkeypatch):
    store._DOWNLOAD_CACHE.clear()
    monkeypatch.setattr(store.config, "CLOUDINARY_CLOUD_NAME", "test-cloud")
    monkeypatch.setattr(store.config, "CLOUDINARY_API_KEY", "server-key")
    monkeypatch.setattr(store.config, "CLOUDINARY_API_SECRET", "server-secret")
    monkeypatch.setattr(store.config, "CLOUDINARY_READ_TIMEOUT_SECONDS", 2)
    monkeypatch.setattr(store, "_CONFIGURED_VALUES", None)


def test_recent_download_is_reused_but_new_version_is_fetched(monkeypatch, cloudinary_config):
    reference = {"provider": "cloudinary", "public_id": "neuralcoco/test/" + "e" * 32,
                 "resource_type": "image", "delivery_type": "authenticated",
                 "format": "jpg", "content_type": "image/jpeg", "version": 1}
    calls = []
    monkeypatch.setattr(store, "_download_asset_once", lambda *args: calls.append(args) or (b"image", "image/jpeg"))
    assert store.download_asset(reference) == store.download_asset(reference)
    assert len(calls) == 1
    store.download_asset({**reference, "version": 2})
    assert len(calls) == 2


def test_expired_download_is_fetched_again(monkeypatch, cloudinary_config):
    reference = {"provider": "cloudinary", "public_id": "neuralcoco/test/" + "f" * 32,
                 "resource_type": "image", "delivery_type": "authenticated",
                 "format": "jpg", "content_type": "image/jpeg"}
    calls = []
    clock = [100]
    monkeypatch.setattr(store.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(store, "_download_asset_once", lambda *args: calls.append(args) or (b"image", "image/jpeg"))
    store.download_asset(reference)
    clock[0] += store._DOWNLOAD_CACHE_TTL + 1
    store.download_asset(reference)
    assert len(calls) == 2


def _jpeg():
    stream = BytesIO()
    Image.new("RGB", (8, 8), "green").save(stream, "JPEG")
    return stream.getvalue()


def _cloudinary_result(options):
    return {"type": "authenticated", "resource_type": "image",
            "public_id": options["public_id"], "asset_id": uuid4().hex,
            "version": 1, "format": "jpg", "bytes": 123}


def test_upload_uses_authenticated_type_and_returns_only_durable_reference(monkeypatch, cloudinary_config):
    calls = []
    monkeypatch.setattr("cloudinary.uploader.upload", lambda data, **options:
                        calls.append((data.read(), options)) or _cloudinary_result(options))
    reference = store.upload_asset(b"jpeg bytes", "image/jpeg", "detection-original")
    content, options = calls[0]
    assert content == b"jpeg bytes"
    assert options["type"] == "authenticated"
    assert options["resource_type"] == "image"
    assert options["overwrite"] is False
    assert options["return_error"] is True
    assert options["timeout"] == store.config.CLOUDINARY_UPLOAD_TIMEOUT_SECONDS
    assert reference["provider"] == "cloudinary"
    assert reference["public_id"].startswith("neuralcoco/detection-original/")
    assert reference["delivery_type"] == "authenticated"
    assert "secure_url" not in reference and "api_secret" not in str(reference)


def test_cloudinary_configures_reusable_pool_and_bounded_upload_timeout(
        monkeypatch, cloudinary_config):
    connector_options = []
    connector = object()
    monkeypatch.setattr("cloudinary.utils.get_http_connector", lambda conf, options:
                        connector_options.append(options) or connector)
    monkeypatch.setattr(store.config, "CLOUDINARY_POOL_MAXSIZE", 8)
    monkeypatch.setattr(store.config, "CLOUDINARY_UPLOAD_TIMEOUT_SECONDS", 90)

    store._configure()

    import cloudinary.uploader as uploader
    assert uploader._http is connector
    assert connector_options[0]["maxsize"] == 8
    assert connector_options[0]["block"] is True
    assert cloudinary.config().timeout == 90


@pytest.mark.parametrize("error,category", [
    (CloudinaryError("write operation timed out"), "timeout"),
    (CloudinaryError("Connection reset by peer"), "connection"),
])
def test_upload_failure_logs_sanitized_category_and_sizes(
        monkeypatch, cloudinary_config, caplog, error, category):
    monkeypatch.setattr("cloudinary.uploader.upload", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    monkeypatch.setattr(store, "_destroy_public_id", lambda public_id: True)
    with pytest.raises(store.CloudinaryStorageError):
        store.upload_asset(
            b"stored", "image/jpeg", "detection-original",
            operation_id="safe-op", source_bytes=12345678,
        )
    assert "operation_id=safe-op" in caplog.text
    assert "source_bytes=12345678" in caplog.text
    assert "stored_bytes=6" in caplog.text
    assert f"error_category={category}" in caplog.text
    assert "api_secret" not in caplog.text.lower()


def test_detection_persists_original_and_display_thumbnail_assets(monkeypatch):
    uploads = []

    def upload(content, content_type, category, **kwargs):
        reference = {"provider": "cloudinary", "public_id": f"neuralcoco/{category}/{uuid4().hex}"}
        uploads.append((content, content_type, category, reference))
        return reference

    monkeypatch.setattr(image_utils, "upload_asset", upload)
    fields = image_utils.save_upload_image_assets(
        "record-1", _jpeg(), np.zeros((8, 8, 3), dtype=np.uint8)
    )

    assert len(uploads) == 2
    assert uploads[0][1:] == ("image/jpeg", "detection-original", fields["image_assets"]["original"])
    assert uploads[1][1:] == ("image/jpeg", "detection-thumbnail", fields["image_assets"]["thumbnail"])
    assert set(fields["image_assets"]) == {"original", "thumbnail"}
    with Image.open(BytesIO(uploads[1][0])) as thumbnail:
        assert thumbnail.format == "JPEG"
        assert thumbnail.size == (8, 8)


def test_detection_thumbnail_failure_keeps_original_asset(monkeypatch, caplog):
    uploads = []

    def upload(content, content_type, category, **kwargs):
        if category == "detection-thumbnail":
            raise image_utils.CloudinaryStorageError("thumbnail unavailable")
        reference = {"provider": "cloudinary", "public_id": f"neuralcoco/{category}/{uuid4().hex}"}
        uploads.append((content, content_type, category, reference))
        return reference

    monkeypatch.setattr(image_utils, "upload_asset", upload)

    fields = image_utils.save_upload_image_assets("record-1", _jpeg())

    assert set(fields["image_assets"]) == {"original"}
    assert len(uploads) == 1
    assert "Detection thumbnail preparation skipped" in caplog.text


def test_detection_thumbnail_generation_failure_keeps_original_asset(monkeypatch, caplog):
    uploads = []
    monkeypatch.setattr(
        image_utils, "_detection_thumbnail",
        lambda _content: (_ for _ in ()).throw(OSError("preview decode failed")),
    )
    monkeypatch.setattr(
        image_utils, "upload_asset",
        lambda content, content_type, category, **kwargs: uploads.append(category) or {
            "provider": "cloudinary", "public_id": f"neuralcoco/{category}/{uuid4().hex}",
        },
    )

    fields = image_utils.save_upload_image_assets("record-1", _jpeg())

    assert fields["image_assets"].keys() == {"original"}
    assert uploads == ["detection-original"]
    assert "Detection thumbnail preparation skipped" in caplog.text


def test_failed_cloudinary_upload_is_sanitized_and_compensated(
        monkeypatch, cloudinary_config, caplog):
    destroyed = []
    monkeypatch.setattr("cloudinary.uploader.upload", lambda *args, **kwargs:
                        (_ for _ in ()).throw(RuntimeError("server-secret leaked by provider")))
    monkeypatch.setattr("cloudinary.uploader.destroy", lambda public_id, **kwargs:
                        destroyed.append((public_id, kwargs)) or {"result": "ok"})
    with pytest.raises(store.CloudinaryStorageError) as error:
        store.upload_asset(b"image", "image/jpeg", "follow-up")
    assert "server-secret" not in str(error.value)
    assert len(destroyed) == 1
    assert destroyed[0][1]["type"] == "authenticated"
    assert "error_type=RuntimeError" in caplog.text
    assert "server-secret" not in caplog.text


def test_upload_logs_provider_http_error_without_sensitive_values(
        monkeypatch, cloudinary_config, caplog):
    destroyed = []
    received_options = []

    def upload(_data, **options):
        received_options.append(options)
        return {"error": {
            "message": "Invalid API key server-key at https://api.cloudinary.com/path?signature=private",
            "http_code": 401,
            "request_id": "request-123",
        }}

    monkeypatch.setattr("cloudinary.uploader.upload", upload)
    monkeypatch.setattr("cloudinary.uploader.destroy", lambda public_id, **options:
                        destroyed.append(public_id) or {"result": "ok"})
    with pytest.raises(store.CloudinaryStorageError, match="upload failed"):
        store.upload_asset(b"image", "image/jpeg", "follow-up")

    assert received_options[0]["return_error"] is True
    assert len(destroyed) == 1
    assert "http_status=401" in caplog.text
    assert "Invalid API key" in caplog.text
    assert "server-key" not in caplog.text
    assert "server-secret" not in caplog.text
    assert "https://" not in caplog.text
    assert "signature=private" not in caplog.text


def test_cloudinary_146_private_download_url_accepts_authenticated_parameters(
        cloudinary_config):
    from cloudinary import utils

    store._configure()
    signed_url = utils.private_download_url(
        "neuralcoco/test/" + "c" * 32,
        "jpg",
        resource_type="image",
        type="authenticated",
        expires_at=2_000_000_000,
    )
    parsed_url = urlsplit(signed_url)
    query = parse_qs(parsed_url.query)

    assert parsed_url.scheme == "https"
    assert parsed_url.hostname == "api.cloudinary.com"
    assert parsed_url.path.endswith("/image/download")
    assert "signature" in query
    assert query["type"] == ["authenticated"]


def test_private_download_failure_logs_status_and_redacts_signed_url(
        monkeypatch, cloudinary_config, caplog):
    reference = {"provider": "cloudinary", "public_id": "neuralcoco/test/" + "d" * 32,
                 "resource_type": "image", "delivery_type": "authenticated",
                 "format": "jpg", "content_type": "image/jpeg"}
    response = SimpleNamespace(status_code=403)
    failure = requests.HTTPError(
        "403 Client Error: Forbidden for url: "
        "https://api.cloudinary.com/v1_1/test-cloud/image/download?signature=private",
        response=response,
    )
    monkeypatch.setattr("cloudinary.utils.private_download_url", lambda *args, **kwargs:
                        "https://api.cloudinary.com/v1_1/test-cloud/image/download?signature=private")
    monkeypatch.setattr(store.requests, "get", lambda *args, **kwargs: (_ for _ in ()).throw(failure))

    with pytest.raises(store.CloudinaryStorageError, match="temporarily unavailable"):
        store.download_asset(reference)

    assert "error_type=HTTPError" in caplog.text
    assert "http_status=403" in caplog.text
    assert "https://" not in caplog.text
    assert "signature=private" not in caplog.text


def test_private_download_retries_timeout_and_transient_503(monkeypatch, cloudinary_config):
    reference = {"provider": "cloudinary", "public_id": "neuralcoco/test/" + "e" * 32,
                 "resource_type": "image", "delivery_type": "authenticated",
                 "format": "jpg", "content_type": "image/jpeg"}
    requested = []
    sleeps = []
    unavailable = SimpleNamespace(
        status_code=503,
        raise_for_status=lambda: (_ for _ in ()).throw(requests.HTTPError("503 unavailable")),
        close=lambda: None,
    )
    recovered = SimpleNamespace(
        status_code=200,
        iter_content=lambda chunk_size: [b"image-body"],
        raise_for_status=lambda: None,
        close=lambda: None,
    )
    responses = [requests.ReadTimeout("read timed out"), unavailable, recovered]

    def get(url, **options):
        requested.append((url, options))
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("cloudinary.utils.private_download_url", lambda *args, **kwargs:
                        "https://api.cloudinary.com/v1_1/test-cloud/image/download?signature=temporary")
    monkeypatch.setattr(store.requests, "get", get)
    monkeypatch.setattr(store.time, "sleep", sleeps.append)

    assert store.download_asset(reference) == (b"image-body", "image/jpeg")
    assert len(requested) == 3
    assert all(options["timeout"] == (5, 2) for _, options in requested)
    assert sleeps == [0.2, 0.5]


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_private_download_does_not_retry_permanent_http_failures(
        monkeypatch, cloudinary_config, status):
    reference = {"provider": "cloudinary", "public_id": "neuralcoco/test/" + "f" * 32,
                 "resource_type": "image", "delivery_type": "authenticated",
                 "format": "jpg", "content_type": "image/jpeg"}
    calls = []
    response = SimpleNamespace(
        status_code=status,
        raise_for_status=lambda: (_ for _ in ()).throw(requests.HTTPError(
            f"{status} provider response", response=SimpleNamespace(status_code=status))),
        close=lambda: None,
    )
    monkeypatch.setattr("cloudinary.utils.private_download_url", lambda *args, **kwargs:
                        "https://api.cloudinary.com/v1_1/test-cloud/image/download?signature=temporary")
    monkeypatch.setattr(store.requests, "get", lambda *args, **kwargs:
                        calls.append(1) or response)

    with pytest.raises(store.CloudinaryStorageError, match="temporarily unavailable"):
        store.download_asset(reference)
    assert calls == [1]


def test_private_download_stops_after_bounded_timeout_retries(
        monkeypatch, cloudinary_config):
    reference = {"provider": "cloudinary", "public_id": "neuralcoco/test/" + "2" * 32,
                 "resource_type": "image", "delivery_type": "authenticated",
                 "format": "jpg", "content_type": "image/jpeg"}
    calls = []
    monkeypatch.setattr("cloudinary.utils.private_download_url", lambda *args, **kwargs:
                        "https://api.cloudinary.com/v1_1/test-cloud/image/download?signature=temporary")
    monkeypatch.setattr(store.requests, "get", lambda *args, **kwargs:
                        calls.append(1) or (_ for _ in ()).throw(requests.ReadTimeout("read timed out")))
    monkeypatch.setattr(store.time, "sleep", lambda _: None)

    with pytest.raises(store.CloudinaryStorageError, match="temporarily unavailable"):
        store.download_asset(reference)
    assert len(calls) == 3


def test_private_download_coalesces_simultaneous_requests(monkeypatch, cloudinary_config):
    reference = {"provider": "cloudinary", "public_id": "neuralcoco/test/" + "1" * 32,
                 "resource_type": "image", "delivery_type": "authenticated",
                 "format": "jpg", "content_type": "image/jpeg"}
    started = threading.Event()
    release = threading.Event()
    calls = []
    response = SimpleNamespace(
        status_code=200,
        iter_content=lambda chunk_size: [b"same-private-image"],
        raise_for_status=lambda: None,
        close=lambda: None,
    )

    def get(*args, **kwargs):
        calls.append(1)
        started.set()
        assert release.wait(timeout=2)
        return response

    monkeypatch.setattr("cloudinary.utils.private_download_url", lambda *args, **kwargs:
                        "https://api.cloudinary.com/v1_1/test-cloud/image/download?signature=temporary")
    monkeypatch.setattr(store.requests, "get", get)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = [pool.submit(store.download_asset, reference) for _ in range(6)]
        assert started.wait(timeout=1)
        # Let the other requests join the in-flight download before it completes.
        assert release.wait(timeout=0.05) is False
        release.set()
        assert [result.result(timeout=2) for result in results] == [
            (b"same-private-image", "image/jpeg")
        ] * 6
    assert len(calls) == 1


def test_authenticated_download_uses_backend_signed_api_request(monkeypatch, cloudinary_config):
    reference = {"provider": "cloudinary", "public_id": "neuralcoco/test/" + "a" * 32,
                 "resource_type": "image", "delivery_type": "authenticated",
                 "format": "jpg", "content_type": "image/jpeg"}
    requested = []
    response = SimpleNamespace(
        iter_content=lambda chunk_size: [b"image-body"],
        raise_for_status=lambda: None,
        close=lambda: None,
    )
    monkeypatch.setattr("cloudinary.utils.private_download_url",
                        lambda public_id, image_format, **kwargs:
                        "https://api.cloudinary.com/v1_1/test-cloud/image/download?signature=temporary")
    monkeypatch.setattr(store.requests, "get", lambda url, **kwargs:
                        requested.append((url, kwargs)) or response)
    content, mime = store.download_asset(reference)
    assert (content, mime) == (b"image-body", "image/jpeg")
    url, options = requested[0]
    assert url.startswith("https://api.cloudinary.com/")
    assert options["allow_redirects"] is False and options["stream"] is True


def test_download_rejects_unexpected_provider_url_before_network(monkeypatch, cloudinary_config):
    reference = {"provider": "cloudinary", "public_id": "neuralcoco/test/" + "b" * 32,
                 "resource_type": "image", "delivery_type": "authenticated",
                 "format": "jpg", "content_type": "image/jpeg"}
    monkeypatch.setattr("cloudinary.utils.private_download_url", lambda *args, **kwargs:
                        "https://attacker.example/image")
    monkeypatch.setattr(store.requests, "get", lambda *args, **kwargs:
                        pytest.fail("Unexpected host must not be contacted"))
    with pytest.raises(store.CloudinaryStorageError, match="invalid download URL"):
        store.download_asset(reference)
