"""Image encoding and durable evidence upload helpers."""
from __future__ import annotations

from io import BytesIO
import hashlib
import logging
import time

import numpy as np
from PIL import Image, UnidentifiedImageError

from app.core import config
from app.infrastructure.cloudinary_storage import (
    CloudinaryStorageError,
    upload_asset,
)

logger = logging.getLogger(__name__)
_JPEG_QUALITIES = (95, 92, 90, 88, 85, 82, 80, 75, 70, 65, 60, 55, 50, 45, 40, 35)
_XMP_IDENTIFIERS = (b"http://ns.adobe.com/xap/1.0/", b"http://ns.adobe.com/xmp/extension/", b"drone-dji", b"<x:xmpmeta")


class DetectionImageProcessingError(CloudinaryStorageError):
    """The storage copy could not be made without damaging evidence metadata."""


def _jpeg_xmp_segments(content: bytes) -> list[bytes]:
    """Return complete JPEG APP1 XMP segments, without decoding private values."""
    if not content.startswith(b"\xff\xd8"):
        return []
    segments: list[bytes] = []
    offset = 2
    while offset + 4 <= len(content) and content[offset] == 0xFF:
        marker_start = offset
        while offset < len(content) and content[offset] == 0xFF:
            offset += 1
        if offset >= len(content) or content[offset] in {0xDA, 0xD9}:
            break
        marker = content[offset]
        offset += 1
        if marker in {0x01} or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(content):
            break
        length = int.from_bytes(content[offset:offset + 2], "big")
        end = offset + length
        if length < 2 or end > len(content):
            break
        segment = content[marker_start:end]
        payload = content[offset + 2:end]
        if marker == 0xE1 and any(identifier.lower() in payload.lower() for identifier in _XMP_IDENTIFIERS):
            segments.append(segment)
        offset = end
    return segments


def _inject_xmp_segments(jpeg: bytes, segments: list[bytes]) -> bytes:
    if not segments:
        return jpeg
    if not jpeg.startswith(b"\xff\xd8"):
        raise DetectionImageProcessingError("Compressed storage copy is not a JPEG")
    return b"\xff\xd8" + b"".join(segments) + jpeg[2:]


def _metadata_snapshot(content: bytes) -> dict:
    try:
        with Image.open(BytesIO(content)) as image:
            exif = image.getexif()
            exif_bytes = image.info.get("exif")
            return {
                "dimensions": tuple(image.size),
                "orientation": exif.get(274),
                "exif_sha256": hashlib.sha256(exif_bytes).hexdigest()
                if exif_bytes else None,
                "icc_sha256": hashlib.sha256(image.info["icc_profile"]).hexdigest()
                if image.info.get("icc_profile") else None,
                "xmp_sha256": [hashlib.sha256(segment).hexdigest()
                               for segment in _jpeg_xmp_segments(content)],
            }
    except Exception as exc:
        raise DetectionImageProcessingError("Detection image metadata could not be read") from exc


def _metadata_matches(original: dict, candidate: dict) -> bool:
    if original["dimensions"] != candidate["dimensions"]:
        return False
    if original["orientation"] != candidate["orientation"]:
        return False
    if original["exif_sha256"] != candidate["exif_sha256"]:
        return False
    if original["icc_sha256"] != candidate["icc_sha256"]:
        return False
    if original["xmp_sha256"] != candidate["xmp_sha256"]:
        return False
    return True


def _prepare_cloudinary_copy(
    content: bytes,
    content_type: str,
    timings: dict[str, float] | None = None,
) -> tuple[bytes, str]:
    """Return a verified storage copy; never resize or alter the original input."""
    if len(content) <= config.DETECTION_STORAGE_MAX_BYTES:
        return content, "source_original"
    if content_type != "image/jpeg":
        raise DetectionImageProcessingError(
            "Only oversized JPEG detection images can be compressed for storage"
        )

    metadata_started = time.perf_counter()
    original_metadata = _metadata_snapshot(content)
    xmp_segments = _jpeg_xmp_segments(content)
    metadata_ms = (time.perf_counter() - metadata_started) * 1000
    compression_ms = 0.0
    try:
        with Image.open(BytesIO(content)) as source:
            source.load()
            exif = source.info.get("exif")
            icc_profile = source.info.get("icc_profile")
            for quality in _JPEG_QUALITIES:
                compression_started = time.perf_counter()
                output = BytesIO()
                options = {"format": "JPEG", "quality": quality, "optimize": True}
                if exif:
                    options["exif"] = exif
                if icc_profile:
                    options["icc_profile"] = icc_profile
                source.save(output, **options)
                candidate = _inject_xmp_segments(output.getvalue(), xmp_segments)
                compression_ms += (time.perf_counter() - compression_started) * 1000
                if len(candidate) > config.DETECTION_STORAGE_MAX_BYTES:
                    continue
                metadata_started = time.perf_counter()
                candidate_metadata = _metadata_snapshot(candidate)
                metadata_ms += (time.perf_counter() - metadata_started) * 1000
                if not _metadata_matches(original_metadata, candidate_metadata):
                    # A lower quality changes only pixels, but encoder behavior
                    # can affect container metadata. Keep searching; never upload
                    # a candidate that failed verification.
                    continue
                if timings is not None:
                    timings["image_compression_ms"] = compression_ms
                    timings["image_metadata_verification_ms"] = metadata_ms
                return candidate, "compressed_jpeg_storage_copy"
    except DetectionImageProcessingError:
        raise
    except Exception as exc:
        raise DetectionImageProcessingError("Detection image compression failed") from exc
    raise DetectionImageProcessingError(
        "JPEG cannot fit the 8 MB storage limit while preserving required metadata"
    )


def _image_content_type(content: bytes) -> str:
    try:
        with Image.open(BytesIO(content)) as image:
            if image.format == "JPEG":
                return "image/jpeg"
            if image.format == "PNG":
                return "image/png"
            if image.format in {"WEBP", "BMP", "TIFF"}:
                return Image.MIME[image.format]
    except (UnidentifiedImageError, OSError):
        pass
    raise CloudinaryStorageError("Detection original must be JPEG, PNG, WebP, BMP or TIFF")


def save_upload_image_assets(
    record_id: str,
    original_bytes: bytes,
    annotated_image: np.ndarray | None = None,
    timings: dict[str, float] | None = None,
    operation_id: str = "unknown",
) -> dict:
    """Upload the byte-identical source image as one authenticated asset.

    Keeping one upload avoids duplicate provider work and preserves image quality,
    EXIF/XMP metadata, and the exact evidence bytes used by the detector.
    Annotation previews are rendered from Firestore metadata in the client.
    ``annotated_image`` remains an accepted argument for callers during the
    transition, but is intentionally ignored. The function never writes local
    files or returns provider URLs.
    """
    del annotated_image
    if not record_id:
        raise CloudinaryStorageError("Detection record identity is required")

    preparation_timings = timings if timings is not None else {}
    validation_started = time.perf_counter()
    content_type = _image_content_type(original_bytes)
    source_sha256 = hashlib.sha256(original_bytes).hexdigest()
    try:
        storage_bytes, representation = _prepare_cloudinary_copy(
            original_bytes, content_type, timings=preparation_timings,
        )
    except DetectionImageProcessingError as exc:
        logger.warning(
            "Detection image processing failed operation_id=%s source_bytes=%d image_type=%s error_category=image_processing",
            operation_id, len(original_bytes), content_type,
        )
        raise exc
    preparation_timings["image_validation_ms"] = (
        time.perf_counter() - validation_started
    ) * 1000
    upload_started = time.perf_counter()
    category = ("detection-original" if representation == "source_original"
                else "detection-storage-copy")
    original = upload_asset(
        storage_bytes, content_type, category, operation_id=operation_id,
        source_bytes=len(original_bytes),
    )
    upload_ms = (time.perf_counter() - upload_started) * 1000
    if timings is not None:
        timings["cloudinary_upload_ms"] = upload_ms
    stored_sha256 = (source_sha256 if storage_bytes is original_bytes
                     else hashlib.sha256(storage_bytes).hexdigest())
    original.update({
        "storage_representation": representation,
        "source_byte_size": len(original_bytes),
        "source_sha256": source_sha256,
        "stored_byte_size": len(storage_bytes),
        "stored_sha256": stored_sha256,
    })
    logger.info(
        "Detection image prepared operation_id=%s source_bytes=%d stored_bytes=%d representation=%s compression_ms=%.0f metadata_verification_ms=%.0f",
        operation_id, len(original_bytes), len(storage_bytes), representation,
        preparation_timings.get("image_compression_ms", 0.0),
        preparation_timings.get("image_metadata_verification_ms", 0.0),
    )
    return {"image_assets": {"original": original}}
