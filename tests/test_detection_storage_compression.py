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


def _jpeg_segment(marker, payload):
    return b"\xff" + bytes([marker]) + (len(payload) + 2).to_bytes(2, "big") + payload


def _with_segments(source, segments, *, after_scan=False):
    if after_scan:
        end = source.rfind(b"\xff\xd9")
        assert end > 0
        return source[:end] + b"".join(segments) + source[end:]
    return source[:2] + b"".join(segments) + source[2:]


def _compressed_copy(monkeypatch, source, limit=500_000):
    monkeypatch.setattr(images.config, "DETECTION_STORAGE_MAX_BYTES", limit)
    stored, representation = images._prepare_cloudinary_copy(source, "image/jpeg")
    assert representation == "compressed_jpeg_storage_copy"
    return stored


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
    assert before_metadata["dji_telemetry"]["relative_altitude_m"] == 35.5
    for field in ("gps", "captured_at", "camera_make", "camera_model", "is_dji", "dji_telemetry"):
        assert after_metadata[field] == before_metadata[field]


def test_large_dji_jpeg_uses_draft_reduction_and_preserves_gps(monkeypatch):
    """Storage is bounded without changing source coordinates or protected DJI metadata."""
    source = photo((1600, 1200), noisy=True)
    monkeypatch.setattr(images.config, "DETECTION_STORAGE_MAX_BYTES", 500_000)
    monkeypatch.setattr(images.config, "CLOUDINARY_MAX_IMAGE_PIXELS", 1_000_000)
    monkeypatch.setattr(images.config, "DETECTION_STORAGE_MAX_PIXELS", 1_000_000)

    stored, representation = images._prepare_cloudinary_copy(source, "image/jpeg", operation_id="large-dji")

    assert representation == "reduced_jpeg_storage_copy"
    assert len(stored) <= 500_000
    with Image.open(BytesIO(stored)) as derivative:
        assert derivative.size == (800, 600)
        assert derivative.width * derivative.height <= 1_000_000
    assert DroneGPS.extract_image_metadata(stored)["gps"] == {"latitude": 7.3, "longitude": 125.6}


def test_storage_asset_records_resized_dimensions(monkeypatch):
    source = photo((1600, 1200), noisy=True)
    monkeypatch.setattr(images.config, "DETECTION_STORAGE_MAX_BYTES", 500_000)
    monkeypatch.setattr(images.config, "CLOUDINARY_MAX_IMAGE_PIXELS", 1_000_000)
    monkeypatch.setattr(images.config, "DETECTION_STORAGE_MAX_PIXELS", 1_000_000)
    monkeypatch.setattr(images, "upload_asset", lambda *_args, **_kwargs: {"provider": "cloudinary"})

    asset = images.save_upload_image_assets("record-large", source)["image_assets"]["original"]

    assert asset["storage_representation"] == "reduced_jpeg_storage_copy"
    assert asset["storage_dimensions"] == {"width": 800, "height": 600}


def test_48mp_dji_dimensions_are_bounded_before_pillow_load(monkeypatch):
    """A 48 MP JPEG selects a 12 MP DCT raster, not a 48 MP Pillow raster."""
    output = BytesIO()
    exif = piexif.dump({"0th": {piexif.ImageIFD.Make: b"DJI"}, "GPS": {
        piexif.GPSIFD.GPSLatitudeRef: b"N", piexif.GPSIFD.GPSLatitude: ((7, 1), (18, 1), (0, 1)),
        piexif.GPSIFD.GPSLongitudeRef: b"E", piexif.GPSIFD.GPSLongitude: ((125, 1), (36, 1), (0, 1)),
    }})
    with Image.new("RGB", (8000, 6000), (20, 120, 30)) as image:
        image.save(output, "JPEG", quality=90, exif=exif)
    source = output.getvalue()
    monkeypatch.setattr(images.config, "DETECTION_STORAGE_MAX_BYTES", 500_000)
    monkeypatch.setattr(images.config, "CLOUDINARY_MAX_IMAGE_PIXELS", 25_000_000)
    monkeypatch.setattr(images.config, "DETECTION_STORAGE_MAX_PIXELS", 12_000_000)

    stored, representation = images._prepare_cloudinary_copy(source, "image/jpeg", operation_id="48mp-dji")

    assert representation == "reduced_jpeg_storage_copy"
    with Image.open(BytesIO(stored)) as derivative:
        assert derivative.size == (4000, 3000)
        assert derivative.width * derivative.height == 12_000_000
    assert DroneGPS.extract_image_metadata(stored)["gps"] == {"latitude": 7.3, "longitude": 125.6}


@pytest.mark.parametrize("dimensions,cap,expected", [
    ((6120, 6035), 12_000_000, (3060, 3018)),
    ((6121, 6035), 12_000_000, (3061, 3018)),
    ((101, 101), 2_500, (26, 26)),
])
def test_odd_jpeg_draft_selects_bounded_dct_raster(dimensions, cap, expected):
    """Exercise Pillow draft selection before decoding a large synthetic raster."""
    output = BytesIO()
    with Image.new("RGB", (8, 8), (20, 120, 30)) as image:
        image.save(output, "JPEG")
    header = bytearray(output.getvalue())
    sof = header.index(b"\xff\xc0")
    header[sof + 5:sof + 7] = dimensions[1].to_bytes(2, "big")
    header[sof + 7:sof + 9] = dimensions[0].to_bytes(2, "big")

    with Image.open(BytesIO(header)) as source:
        assert source.size == dimensions
        target = images._storage_draft_size(*dimensions, cap)
        source.draft("RGB", target)
        assert source.size == expected
        assert source.width * source.height <= cap


def test_odd_jpeg_storage_derivative_preserves_metadata_and_byte_cap(monkeypatch):
    source = photo((101, 101), metadata=True)
    monkeypatch.setattr(images.config, "CLOUDINARY_MAX_IMAGE_PIXELS", 2_500)
    monkeypatch.setattr(images.config, "DETECTION_STORAGE_MAX_PIXELS", 2_500)
    monkeypatch.setattr(images.config, "DETECTION_STORAGE_MAX_BYTES", 8_000_000)

    stored, representation = images._prepare_cloudinary_copy(source, "image/jpeg")

    assert representation == "reduced_jpeg_storage_copy"
    assert len(stored) <= images.config.DETECTION_STORAGE_MAX_BYTES
    with Image.open(BytesIO(source)) as original, Image.open(BytesIO(stored)) as derivative:
        assert original.size == (101, 101)
        assert derivative.size == (26, 26)
        assert derivative.width * derivative.height <= images.config.DETECTION_STORAGE_MAX_PIXELS
    assert images._jpeg_protected_segments(stored) == images._jpeg_protected_segments(source)
    assert DroneGPS.extract_image_metadata(stored)["gps"] == DroneGPS.extract_image_metadata(source)["gps"]


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


def test_jpeg_trailing_data_does_not_block_storage_compression(monkeypatch):
    source = photo((1000, 1000), noisy=True, metadata=False) + b"camera-export-padding"

    stored = _compressed_copy(monkeypatch, source)

    assert stored.endswith(b"\xff\xd9")
    with Image.open(BytesIO(stored)) as image:
        assert image.size == (1000, 1000)


def test_storage_copy_rejects_any_exif_payload_change():
    source = photo()
    original = images._metadata_snapshot(source)
    changed = dict(original)
    changed["protected_segments"] = list(original["protected_segments"])
    kind, size, _digest = changed["protected_segments"][0]
    changed["protected_segments"][0] = (kind, size, "0" * 64)

    assert images._metadata_matches(original, changed) is False


def test_xmp_after_scan_is_preserved_byte_identically(monkeypatch):
    source = photo((1000, 1000), noisy=True, metadata=False)
    xmp = _jpeg_segment(
        0xE1, images._XMP_HEADER + b'<x:xmpmeta xmlns:x="adobe:ns:meta/">DJI</x:xmpmeta>',
    )
    source = _with_segments(source, [xmp], after_scan=True)

    stored = _compressed_copy(monkeypatch, source)

    assert xmp in source and xmp in stored
    assert images._jpeg_protected_segments(source) == images._jpeg_protected_segments(stored)


def test_multiple_exif_segments_are_preserved_byte_identically(monkeypatch):
    source = photo((1000, 1000), noisy=True)
    extra_exif = _jpeg_segment(
        0xE1, piexif.dump({"0th": {piexif.ImageIFD.Software: b"NeuralCoco-audit"}}),
    )
    source = _with_segments(source, [extra_exif])

    stored = _compressed_copy(monkeypatch, source)
    source_exif = [raw for kind, raw in images._jpeg_protected_segments(source) if kind == "exif"]
    stored_exif = [raw for kind, raw in images._jpeg_protected_segments(stored) if kind == "exif"]

    assert len(source_exif) == 2
    assert stored_exif == source_exif


def test_multiple_icc_segments_are_preserved_byte_identically(monkeypatch):
    source = photo((1000, 1000), noisy=True, metadata=False)
    icc = [
        _jpeg_segment(0xE2, images._ICC_HEADER + bytes([1, 2]) + b"first-half"),
        _jpeg_segment(0xE2, images._ICC_HEADER + bytes([2, 2]) + b"second-half"),
    ]
    source = _with_segments(source, icc)

    stored = _compressed_copy(monkeypatch, source)
    stored_icc = [raw for kind, raw in images._jpeg_protected_segments(stored) if kind == "icc"]

    assert stored_icc == icc


def test_extended_dji_xmp_chunks_are_preserved_byte_identically(monkeypatch):
    source = photo((1000, 1000), noisy=True, metadata=False)
    guid = b"0123456789ABCDEF0123456789ABCDEF"
    body = b'<drone-dji:RelativeAltitude>35.5</drone-dji:RelativeAltitude>'
    split = len(body) // 2
    standard = _jpeg_segment(
        0xE1, images._XMP_HEADER + b'<x:xmpmeta xmpNote:HasExtendedXMP="' + guid + b'"/>',
    )
    extended = [
        _jpeg_segment(0xE1, images._EXTENDED_XMP_HEADER + guid
                      + len(body).to_bytes(4, "big") + (0).to_bytes(4, "big") + body[:split]),
        _jpeg_segment(0xE1, images._EXTENDED_XMP_HEADER + guid
                      + len(body).to_bytes(4, "big") + split.to_bytes(4, "big") + body[split:]),
    ]
    protected = [standard, *extended]
    source = _with_segments(source, protected, after_scan=True)

    stored = _compressed_copy(monkeypatch, source)

    assert [raw for _, raw in images._jpeg_protected_segments(stored)] == protected


def test_progressive_multiscan_jpeg_metadata_is_preserved(monkeypatch):
    pixels = np.random.default_rng(7).integers(0, 256, (1000, 1000, 3), dtype=np.uint8)
    output = BytesIO()
    Image.fromarray(pixels, "RGB").save(output, "JPEG", quality=100, progressive=True)
    source = output.getvalue()
    assert source.count(b"\xff\xda") > 1
    xmp = _jpeg_segment(0xE1, images._XMP_HEADER + b"<x:xmpmeta>multiscan</x:xmpmeta>")
    source = _with_segments(source, [xmp], after_scan=True)

    stored = _compressed_copy(monkeypatch, source)

    assert xmp in stored
    assert images._jpeg_protected_segments(source) == images._jpeg_protected_segments(stored)


@pytest.mark.parametrize("change", ["missing", "reordered"])
def test_missing_or_reordered_protected_segments_fail_verification(change):
    source = photo(metadata=False)
    segments = [
        ("exif", _jpeg_segment(0xE1, piexif.dump({"0th": {piexif.ImageIFD.Make: b"DJI"}}))),
        ("xmp", _jpeg_segment(0xE1, images._XMP_HEADER + b"<x:xmpmeta/>")),
    ]
    source = images._inject_protected_segments(source, segments)
    candidate_segments = segments[:-1] if change == "missing" else list(reversed(segments))
    candidate = images._inject_protected_segments(photo(metadata=False), candidate_segments)

    assert images._metadata_matches(
        images._metadata_snapshot(source), images._metadata_snapshot(candidate),
    ) is False


@pytest.mark.parametrize("layout", ["bad-length", "unknown-app1", "reordered-icc"])
def test_malformed_or_unsupported_marker_layout_is_rejected(monkeypatch, layout):
    source = photo((1000, 1000), noisy=True, metadata=False)
    if layout == "bad-length":
        source = source[:2] + b"\xff\xe1\x00\x01" + source[2:]
    elif layout == "unknown-app1":
        source = _with_segments(source, [_jpeg_segment(0xE1, b"vendor-private")])
    else:
        source = _with_segments(source, [
            _jpeg_segment(0xE2, images._ICC_HEADER + bytes([2, 2]) + b"second"),
            _jpeg_segment(0xE2, images._ICC_HEADER + bytes([1, 2]) + b"first"),
        ])
    monkeypatch.setattr(images.config, "DETECTION_STORAGE_MAX_BYTES", 500_000)
    monkeypatch.setattr(images, "upload_asset", lambda *_args, **_kwargs:
                        pytest.fail("Unverified evidence must not be uploaded"))

    with pytest.raises(images.DetectionImageProcessingError):
        images.save_upload_image_assets("unsafe-layout", source)


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
