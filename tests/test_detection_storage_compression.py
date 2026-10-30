"""Detection storage preserves source evidence and avoids redundant uploads."""
from io import BytesIO
import hashlib

import numpy as np
import piexif
import pytest
from PIL import Image

from app.infrastructure.imaging import image_utils as images
from app.services import detection_service as service
from drone_gps import DroneGPS
from test_detection_cloudinary_persistence import MemoryFirestore


def photo(size=(80, 60), noisy=False, metadata=True):
    exif = piexif.dump({
        "0th": {piexif.ImageIFD.Make: b"DJI", piexif.ImageIFD.Model: b"FC-Test",
                piexif.ImageIFD.Orientation: 6},
        "Exif": {piexif.ExifIFD.DateTimeOriginal: b"2026:10:26 11:59:47"},
        "GPS": {piexif.GPSIFD.GPSLatitudeRef: b"N",
                piexif.GPSIFD.GPSLatitude: ((7, 1), (18, 1), (0, 1)),
                piexif.GPSIFD.GPSLongitudeRef: b"E",
                piexif.GPSIFD.GPSLongitude: ((125, 1), (36, 1), (0, 1))},
    }) if metadata else b""
    output = BytesIO()
    pixels = (np.random.default_rng(42).integers(0, 256, (size[1], size[0], 3), dtype=np.uint8)
              if noisy else np.full((size[1], size[0], 3), (0, 128, 0), dtype=np.uint8))
    with Image.fromarray(pixels, "RGB") as image:
        image.save(output, "JPEG", quality=100, exif=exif,
                   icc_profile=(b"test-icc-profile" * 10 if metadata else None),
                   xmp=(b'<x:xmpmeta xmlns:x="adobe:ns:meta/" drone-dji:RelativeAltitude="35.5"/>'
                        if metadata else None))
    return output.getvalue()


def test_source_is_uploaded_once_and_byte_identical(monkeypatch):
    source = photo()
    uploads = []
    reference = {"provider": "cloudinary"}
    monkeypatch.setattr(images, "upload_asset", lambda data, mime, category, **kwargs:
                        uploads.append((data, mime, category, kwargs)) or reference)
    timings = {}
    result = images.save_upload_image_assets("record-1", source, timings=timings)
    asset = result["image_assets"]["original"]
    assert uploads[0][:3] == (source, "image/jpeg", "detection-original")
    assert uploads[0][3]["source_bytes"] == len(source)
    assert asset["storage_representation"] == "source_original"
    assert asset["source_sha256"] == asset["stored_sha256"] == hashlib.sha256(source).hexdigest()
    assert asset["source_byte_size"] == asset["stored_byte_size"] == len(source)
    assert timings["image_validation_ms"] >= 0
    assert timings["cloudinary_upload_ms"] >= 0
    assert DroneGPS.extract_image_metadata(uploads[0][0])["gps"] == {
        "latitude": 7.3, "longitude": 125.6,
    }


def test_oversized_jpeg_is_compressed_without_resizing_or_metadata_loss(monkeypatch):
    source = photo((3200, 3200), noisy=True)
    assert len(source) > 8_000_000
    uploads = []
    monkeypatch.setattr(images, "upload_asset", lambda data, mime, category, **kwargs:
                        uploads.append(data) or {"provider": "cloudinary"})

    timings = {}
    result = images.save_upload_image_assets(
        "record-large", source, timings=timings, operation_id="safe-op",
    )
    stored = uploads[0]
    asset = result["image_assets"]["original"]

    assert len(stored) <= 8_000_000 < len(source)
    assert asset["storage_representation"] == "compressed_jpeg_storage_copy"
    assert asset["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert asset["stored_sha256"] == hashlib.sha256(stored).hexdigest()
    assert asset["source_sha256"] != asset["stored_sha256"]
    assert timings["image_compression_ms"] >= 0
    assert timings["image_metadata_verification_ms"] >= 0
    with Image.open(BytesIO(source)) as before, Image.open(BytesIO(stored)) as after:
        assert after.size == before.size == (3200, 3200)
        assert after.getexif().get(274) == before.getexif().get(274) == 6
        assert after.info.get("icc_profile") == before.info.get("icc_profile")
    before_metadata = DroneGPS.extract_image_metadata(source)
    after_metadata = DroneGPS.extract_image_metadata(stored)
    for field in ("gps", "captured_at", "camera_make", "camera_model", "is_dji", "dji_telemetry"):
        assert after_metadata[field] == before_metadata[field]


def test_missing_metadata_is_not_invented_during_compression(monkeypatch):
    source = photo((1000, 1000), noisy=True, metadata=False)
    monkeypatch.setattr(images.config, "DETECTION_STORAGE_MAX_BYTES", 500_000)
    stored, representation = images._prepare_cloudinary_copy(source, "image/jpeg")
    assert representation == "compressed_jpeg_storage_copy"
    metadata = DroneGPS.extract_image_metadata(stored)
    assert metadata["gps"] is None
    assert metadata["captured_at"] is None
    assert metadata["camera_make"] is None
    assert metadata["dji_telemetry"] == {}


def test_storage_copy_rejects_any_exif_payload_change():
    source = photo()
    original = images._metadata_snapshot(source)
    changed = dict(original)
    changed["exif_sha256"] = "0" * 64

    assert images._metadata_matches(original, changed) is False


def test_unattainable_limit_rejects_copy_without_upload(monkeypatch):
    source = photo((80, 60))
    monkeypatch.setattr(images.config, "DETECTION_STORAGE_MAX_BYTES", 1)
    monkeypatch.setattr(images, "upload_asset", lambda *args, **kwargs:
                        pytest.fail("Invalid storage copy must not upload"))
    with pytest.raises(images.DetectionImageProcessingError, match="cannot fit"):
        images.save_upload_image_assets("record-1", source, operation_id="safe-op")


def test_persistence_records_cloudinary_and_firestore_stage_times(monkeypatch):
    source = photo()
    db = MemoryFirestore()
    monkeypatch.setattr(service, "_get_firestore", lambda: (db, True))
    monkeypatch.setattr(service, "invalidate_expert_records_cache", lambda: None)
    monkeypatch.setattr(images, "upload_asset", lambda *args, **kwargs: {"provider": "cloudinary"})
    timings = {}
    saved = service.save_detection_record(
        user_id="farmer", email="farmer@example.test", record_id="record-1",
        detections=[{"class": "Bud Rot", "confidence": 0.8, "bbox": [6, 8, 30, 40]}],
        gps_data={"latitude": 7.3, "longitude": 125.6}, filename="drone.jpg",
        source="upload", image_metadata=DroneGPS.extract_image_metadata(source),
        original_bytes=source, annotated_image=None, is_offline=False,
        image_dimensions={"width": 80, "height": 60}, timings=timings,
    )
    assert saved is True
    assert db.record["image_assets"]["original"]["provider"] == "cloudinary"
    assert db.record["image_assets"]["original"]["storage_representation"] == "source_original"
    assert timings["cloudinary_upload_ms"] >= 0
    assert timings["firestore_write_ms"] >= 0
