"""Uploaded detection records must not be created after an inference failure."""

import asyncio
import sys
from io import BytesIO
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi import HTTPException, UploadFile

from app.api import detections


def _image() -> bytes:
    success, encoded = cv2.imencode(".jpg", np.zeros((16, 16, 3), dtype=np.uint8))
    assert success
    return encoded.tobytes()


class _Gps:
    @staticmethod
    def extract_image_metadata(*_args):
        return {}

    @staticmethod
    def extract_gps_from_image(*_args):
        return None


def _configure_upload(monkeypatch, detector):
    async def allowed(*_args, **_kwargs):
        return None

    monkeypatch.setattr(detections.limiter, "rolling", allowed)
    monkeypatch.setattr(detections.limiter, "image_quota_24h", allowed)
    monkeypatch.setattr("app.infrastructure.firebase.auth.verify_token", lambda _token: {"uid": "farmer-a"})
    monkeypatch.setattr("drone_gps.get_drone_gps", lambda: _Gps())
    monkeypatch.setattr("drone_gps.init_drone_gps", lambda: _Gps())
    monkeypatch.setitem(sys.modules, "model", SimpleNamespace(detector=detector))


def _call_detect(contents=None, content_type="image/jpeg", headers=None):
    return asyncio.run(detections.detect_image(
        request=SimpleNamespace(headers={"Authorization": "Bearer token", **(headers or {})}),
        file=UploadFile(filename="leaf.png" if content_type == "image/png" else "leaf.jpg",
                        file=BytesIO(_image() if contents is None else contents),
                        headers={"content-type": content_type}),
    ))


def test_failed_inference_does_not_create_detection_record(monkeypatch):
    saved = []
    detector = SimpleNamespace(predict=lambda image, conf: {
        "detections": [], "image": image, "inference_status": "failed",
    })
    _configure_upload(monkeypatch, detector)
    monkeypatch.setattr(detections, "save_detection_record", lambda *args, **kwargs: saved.append((args, kwargs)))

    with pytest.raises(HTTPException) as error:
        _call_detect()

    assert error.value.status_code == 503
    assert saved == []


def test_genuine_no_detection_result_remains_a_successful_analysis(monkeypatch):
    detector = SimpleNamespace(
        backend="test", active_model_path=None, class_names={},
        predict=lambda image, conf: {"detections": [], "image": image, "inference_status": "success"},
    )
    _configure_upload(monkeypatch, detector)
    monkeypatch.setattr(detections, "save_detection_record",
                        lambda *args, **kwargs: pytest.fail("No-detection result must not create a record"))

    result = _call_detect()

    assert result["detections"] == []
    assert result["recorded_count"] == 0
    assert result["message"] == "0 detections found (0 saved)"


@pytest.mark.parametrize("with_content_length", [False, True])
def test_image_larger_than_old_ten_mb_limit_reaches_inference(monkeypatch, with_content_length):
    pixels = np.random.default_rng(42).integers(0, 256, (2048, 2048, 3), dtype=np.uint8)
    success, encoded = cv2.imencode(".png", pixels)
    assert success
    contents = encoded.tobytes()
    assert 10_000_000 < len(contents) < detections.MAX_IMAGE_UPLOAD_BYTES
    detector = SimpleNamespace(
        backend="test", active_model_path=None, class_names={},
        predict=lambda image, conf: {"detections": [], "image": image, "inference_status": "success"},
    )
    _configure_upload(monkeypatch, detector)
    headers = {"content-length": str(len(contents) + 500)} if with_content_length else {}

    result = _call_detect(contents, "image/png", headers)

    assert result["image_dimensions"] == {"width": 2048, "height": 2048}
    assert result["message"] == "0 detections found (0 saved)"


@pytest.mark.parametrize("with_content_length", [False, True])
def test_upload_still_enforces_configured_byte_limit(monkeypatch, with_content_length):
    _configure_upload(monkeypatch, SimpleNamespace())
    monkeypatch.setattr(detections, "MAX_IMAGE_UPLOAD_BYTES", 4)
    headers = {"content-length": "1000005"} if with_content_length else {}

    with pytest.raises(HTTPException) as error:
        _call_detect(headers=headers)

    assert error.value.status_code == 413


@pytest.mark.parametrize("with_metadata", [False, True])
def test_48_megapixel_drone_photo_and_optional_metadata(monkeypatch, with_metadata):
    import piexif
    from PIL import Image
    from drone_gps import DroneGPS

    exif = piexif.dump({
        "0th": {piexif.ImageIFD.Make: b"DJI", piexif.ImageIFD.Model: b"Drone camera"},
        "Exif": {piexif.ExifIFD.DateTimeOriginal: b"2026:10:26 11:59:47"},
        "GPS": {
            piexif.GPSIFD.GPSLatitudeRef: b"N",
            piexif.GPSIFD.GPSLatitude: ((7, 1), (18, 1), (0, 1)),
            piexif.GPSIFD.GPSLongitudeRef: b"E",
            piexif.GPSIFD.GPSLongitude: ((125, 1), (36, 1), (0, 1)),
        },
    }) if with_metadata else b""
    output = BytesIO()
    with Image.new("RGB", (8000, 6000), "green") as photo:
        photo.save(output, format="JPEG", exif=exif)
    detector = SimpleNamespace(
        backend="test", active_model_path=None, class_names={},
        predict=lambda image, conf: {"detections": [], "image": image, "inference_status": "success"},
    )
    _configure_upload(monkeypatch, detector)
    gps = DroneGPS()
    monkeypatch.setattr("drone_gps.get_drone_gps", lambda: gps)
    monkeypatch.setattr(detections, "set_initial_farm_center", lambda *args: None)

    result = _call_detect(output.getvalue())

    assert result["image_dimensions"] == {"width": 8000, "height": 6000}
    metadata = result["image_metadata"]
    assert metadata["metadata_available"] is with_metadata
    if with_metadata:
        assert metadata["camera_make"] == "DJI"
        assert metadata["captured_at"] == "2026:10:26 11:59:47"
        assert result["gps_lat"] == pytest.approx(7.3)
        assert result["gps_lng"] == pytest.approx(125.6)
    else:
        assert result["gps_lat"] is None
        assert result["gps_lng"] is None


def test_upload_still_enforces_pixel_limit(monkeypatch):
    _configure_upload(monkeypatch, SimpleNamespace())
    monkeypatch.setattr(detections, "MAX_IMAGE_UPLOAD_PIXELS", 100)
    with pytest.raises(HTTPException) as error:
        _call_detect()
    assert error.value.status_code == 400
    assert "megapixels" in error.value.detail


@pytest.mark.parametrize("format", ["JPEG", "PNG", "WEBP", "BMP", "TIFF"])
def test_actual_image_format_with_generic_mime_reaches_inference(monkeypatch, format):
    from PIL import Image
    output = BytesIO()
    Image.new("RGB", (24, 16), "green").save(output, format=format)
    detector = SimpleNamespace(
        backend="test", active_model_path=None, class_names={},
        predict=lambda image, conf: {"detections": [], "image": image, "inference_status": "success"},
    )
    _configure_upload(monkeypatch, detector)
    result = _call_detect(output.getvalue(), "application/octet-stream")
    assert result["image_dimensions"] == {"width": 24, "height": 16}


def test_120_megapixel_jpeg_header_passes_validation(monkeypatch):
    from PIL import Image
    output = BytesIO()
    with Image.new("L", (12000, 10000)) as source:
        source.save(output, format="JPEG")
    _configure_upload(monkeypatch, SimpleNamespace())
    # Stop after header validation, before allocating the full RGB inference image.
    class Validated(Exception):
        pass
    def reached_metadata(*args):
        raise Validated()
    monkeypatch.setattr(_Gps, "extract_image_metadata", reached_metadata)
    with pytest.warns(Image.DecompressionBombWarning):
        with pytest.raises(Validated):
            _call_detect(output.getvalue())


def test_non_image_is_rejected_even_with_jpeg_mime(monkeypatch):
    _configure_upload(monkeypatch, SimpleNamespace())
    with pytest.raises(HTTPException) as error:
        _call_detect(b"not an image", "image/jpeg")
    assert error.value.status_code == 400


def test_mpo_primary_frame_is_converted_for_inference_with_exif_preserved(monkeypatch):
    import piexif
    from PIL import Image
    from test_detection_storage_compression import photo

    primary_bytes = photo((32, 24))
    with Image.open(BytesIO(primary_bytes)) as primary:
        secondary = Image.new("RGB", primary.size, "red")
        output = BytesIO()
        primary.save(output, format="MPO", save_all=True, append_images=[secondary],
                     exif=primary.info.get("exif"))
        secondary.close()
    mpo_bytes = output.getvalue()
    with Image.open(BytesIO(mpo_bytes)) as mpo:
        assert mpo.format == "MPO"
        assert mpo.n_frames == 2

    seen = []
    saved = []
    def predict(image, conf):
        seen.append(image.copy())
        return {"detections": [{"class": "Bud Rot", "confidence": 0.9}],
                "image": image, "inference_status": "success"}
    detector = SimpleNamespace(backend="test", active_model_path=None, class_names={}, predict=predict)
    _configure_upload(monkeypatch, detector)
    from drone_gps import DroneGPS
    gps = DroneGPS()
    monkeypatch.setattr("drone_gps.get_drone_gps", lambda: gps)
    monkeypatch.setattr(detections, "set_initial_farm_center", lambda *_args: None)
    monkeypatch.setattr(detections, "save_detection_record",
                        lambda **kwargs: saved.append(kwargs))
    result = _call_detect(mpo_bytes)

    assert len(seen) == 1
    # OpenCV applies EXIF orientation while retaining the same pixel count.
    assert seen[0].shape[:2] == (32, 24)
    assert result["image_dimensions"] == {"width": 24, "height": 32}
    assert result["gps_lat"] == pytest.approx(7.3)
    assert result["gps_lng"] == pytest.approx(125.6)
    assert result["gps_source"] == "dji_exif"
    assert len(saved) == 1
    with Image.open(BytesIO(saved[0]["original_bytes"])) as normalized:
        assert normalized.format == "JPEG"
        assert normalized.size == (32, 24)
        assert normalized.getexif()[274] == 6
    normalized_gps = gps.extract_gps_from_image(saved[0]["original_bytes"], "DJI_0119.JPG")
    assert normalized_gps.latitude == pytest.approx(7.3)
    assert normalized_gps.longitude == pytest.approx(125.6)
