"""Detection storage preserves source evidence and avoids redundant uploads."""
from io import BytesIO

import piexif
from PIL import Image

from app.infrastructure.imaging import image_utils as images
from app.services import detection_service as service
from drone_gps import DroneGPS
from test_detection_cloudinary_persistence import MemoryFirestore


def photo(size=(80, 60)):
    exif = piexif.dump({
        "0th": {piexif.ImageIFD.Make: b"DJI", piexif.ImageIFD.Orientation: 6},
        "Exif": {piexif.ExifIFD.DateTimeOriginal: b"2026:10:26 11:59:47"},
        "GPS": {piexif.GPSIFD.GPSLatitudeRef: b"N",
                piexif.GPSIFD.GPSLatitude: ((7, 1), (18, 1), (0, 1)),
                piexif.GPSIFD.GPSLongitudeRef: b"E",
                piexif.GPSIFD.GPSLongitude: ((125, 1), (36, 1), (0, 1))},
    })
    output = BytesIO()
    with Image.new("RGB", size, "green") as image:
        image.save(output, "JPEG", quality=100, exif=exif,
                   xmp=b'<x:xmpmeta xmlns:x="adobe:ns:meta/" drone-dji:RelativeAltitude="35.5"/>')
    return output.getvalue()


def test_source_is_uploaded_once_and_byte_identical(monkeypatch):
    source = photo()
    uploads = []
    reference = {"provider": "cloudinary"}
    monkeypatch.setattr(images, "upload_asset", lambda data, mime, category:
                        uploads.append((data, mime, category)) or reference)
    timings = {}
    result = images.save_upload_image_assets("record-1", source, timings=timings)
    assert result == {"image_assets": {"original": reference}}
    assert uploads == [(source, "image/jpeg", "detection-original")]
    assert timings["image_validation_ms"] >= 0
    assert timings["cloudinary_upload_ms"] >= 0
    assert DroneGPS.extract_image_metadata(uploads[0][0])["gps"] == {
        "latitude": 7.3, "longitude": 125.6,
    }


def test_persistence_records_cloudinary_and_firestore_stage_times(monkeypatch):
    source = photo()
    db = MemoryFirestore()
    monkeypatch.setattr(service, "_get_firestore", lambda: (db, True))
    monkeypatch.setattr(service, "invalidate_expert_records_cache", lambda: None)
    monkeypatch.setattr(images, "upload_asset", lambda *args: {"provider": "cloudinary"})
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
    assert db.record["image_assets"] == {"original": {"provider": "cloudinary"}}
    assert timings["cloudinary_upload_ms"] >= 0
    assert timings["firestore_write_ms"] >= 0
