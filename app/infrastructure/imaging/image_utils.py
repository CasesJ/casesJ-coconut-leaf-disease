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
_XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
_EXTENDED_XMP_HEADER = b"http://ns.adobe.com/xmp/extension/\x00"
_ICC_HEADER = b"ICC_PROFILE\x00"
_MAX_JPEG_MARKERS = 100_000
_MAX_PROTECTED_METADATA_BYTES = 16 * 1024 * 1024


class DetectionImageProcessingError(CloudinaryStorageError):
    """The storage copy could not be made without damaging evidence metadata."""


def _protected_segment_kind(marker: int, payload: bytes) -> str | None:
    if marker == 0xE1:
        if payload.startswith(b"Exif\x00\x00"):
            return "exif"
        if payload.startswith(_XMP_HEADER):
            return "xmp"
        if payload.startswith(_EXTENDED_XMP_HEADER):
            return "extended_xmp"
        # Unknown APP1 data cannot safely be reconstructed after re-encoding.
        raise DetectionImageProcessingError("JPEG contains unsupported APP1 metadata")
    if marker == 0xE2:
        if payload.startswith(_ICC_HEADER):
            return "icc"
        # APP2 is also used by FlashPix and vendor-specific metadata. Fail closed.
        raise DetectionImageProcessingError("JPEG contains unsupported APP2 metadata")
    return None


def _raw_segment_payload(raw: bytes) -> bytes:
    marker_offset = 0
    while marker_offset < len(raw) and raw[marker_offset] == 0xFF:
        marker_offset += 1
    return raw[marker_offset + 3:]


def _validate_protected_layout(segments: list[tuple[str, bytes]]) -> None:
    icc_segments = [raw for kind, raw in segments if kind == "icc"]
    if icc_segments:
        sequence = []
        for raw in icc_segments:
            payload = _raw_segment_payload(raw)
            if len(payload) < len(_ICC_HEADER) + 2:
                raise DetectionImageProcessingError("JPEG contains malformed ICC metadata")
            sequence.append((payload[len(_ICC_HEADER)], payload[len(_ICC_HEADER) + 1]))
        expected_count = len(icc_segments)
        if sequence != [(index, expected_count) for index in range(1, expected_count + 1)]:
            raise DetectionImageProcessingError("JPEG contains ambiguous ICC metadata ordering")

    extended_groups: dict[bytes, tuple[int, list[tuple[int, int]]]] = {}
    header_size = len(_EXTENDED_XMP_HEADER)
    for kind, raw in segments:
        if kind != "extended_xmp":
            continue
        payload = _raw_segment_payload(raw)
        if len(payload) < header_size + 40:
            raise DetectionImageProcessingError("JPEG contains malformed extended XMP metadata")
        guid = payload[header_size:header_size + 32]
        try:
            int(guid, 16)
        except ValueError:
            raise DetectionImageProcessingError("JPEG contains malformed extended XMP metadata") from None
        total = int.from_bytes(payload[header_size + 32:header_size + 36], "big")
        offset = int.from_bytes(payload[header_size + 36:header_size + 40], "big")
        chunk_size = len(payload) - header_size - 40
        if total <= 0 or offset + chunk_size > total:
            raise DetectionImageProcessingError("JPEG contains malformed extended XMP metadata")
        key = guid.lower()
        if key in extended_groups and extended_groups[key][0] != total:
            raise DetectionImageProcessingError("JPEG contains inconsistent extended XMP metadata")
        extended_groups.setdefault(key, (total, []))[1].append((offset, offset + chunk_size))
    for total, ranges in extended_groups.values():
        if sorted(ranges) != ranges or not ranges or ranges[0][0] != 0:
            raise DetectionImageProcessingError("JPEG contains ambiguous extended XMP ordering")
        if any(previous[1] != current[0] for previous, current in zip(ranges, ranges[1:])):
            raise DetectionImageProcessingError("JPEG contains incomplete extended XMP metadata")
        if ranges[-1][1] != total:
            raise DetectionImageProcessingError("JPEG contains incomplete extended XMP metadata")


def _jpeg_protected_segments(content: bytes) -> list[tuple[str, bytes]]:
    """Inventory protected JPEG metadata across all structurally valid scans."""
    if not content.startswith(b"\xff\xd8"):
        raise DetectionImageProcessingError("Compressed storage copy is not a JPEG")
    segments: list[tuple[str, bytes]] = []
    protected_bytes = 0
    marker_count = 0
    offset = 2
    in_scan = False
    found_eoi = False

    while offset < len(content):
        if in_scan:
            marker_start = content.find(b"\xff", offset)
            if marker_start < 0:
                break
            offset = marker_start + 1
            while offset < len(content) and content[offset] == 0xFF:
                offset += 1
            if offset >= len(content):
                break
            marker = content[offset]
            if marker == 0x00 or 0xD0 <= marker <= 0xD7:
                offset += 1
                continue
            offset = marker_start
            in_scan = False
            continue

        marker_start = offset
        if content[offset] != 0xFF:
            raise DetectionImageProcessingError("JPEG contains an unsupported marker layout")
        while offset < len(content) and content[offset] == 0xFF:
            offset += 1
        if offset >= len(content):
            break
        marker = content[offset]
        offset += 1
        marker_count += 1
        if marker_count > _MAX_JPEG_MARKERS:
            raise DetectionImageProcessingError("JPEG contains too many markers")
        if marker == 0x00 or marker == 0xD8:
            raise DetectionImageProcessingError("JPEG contains an unsupported marker layout")
        if marker == 0xD9:
            found_eoi = True
            if offset != len(content):
                raise DetectionImageProcessingError("JPEG contains unsupported trailing data")
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(content):
            raise DetectionImageProcessingError("JPEG contains a truncated marker")
        length = int.from_bytes(content[offset:offset + 2], "big")
        end = offset + length
        if length < 2 or end > len(content):
            raise DetectionImageProcessingError("JPEG contains an invalid marker length")
        payload = content[offset + 2:end]
        kind = _protected_segment_kind(marker, payload)
        if kind is not None:
            raw = content[marker_start:end]
            protected_bytes += len(raw)
            if protected_bytes > _MAX_PROTECTED_METADATA_BYTES:
                raise DetectionImageProcessingError("JPEG protected metadata is too large")
            segments.append((kind, raw))
        offset = end
        if marker == 0xDA:
            in_scan = True

    if not found_eoi:
        raise DetectionImageProcessingError("JPEG is missing its end marker")
    _validate_protected_layout(segments)
    return segments


def _inject_protected_segments(jpeg: bytes, segments: list[tuple[str, bytes]]) -> bytes:
    if not segments:
        return jpeg
    if not jpeg.startswith(b"\xff\xd8"):
        raise DetectionImageProcessingError("Compressed storage copy is not a JPEG")
    return b"\xff\xd8" + b"".join(raw for _, raw in segments) + jpeg[2:]


def _metadata_snapshot(
    content: bytes,
    protected_segments: list[tuple[str, bytes]] | None = None,
) -> dict:
    try:
        with Image.open(BytesIO(content)) as image:
            exif = image.getexif()
            inventory = (protected_segments if protected_segments is not None
                         else _jpeg_protected_segments(content))
            return {
                "dimensions": tuple(image.size),
                "orientation": exif.get(274),
                "protected_segments": [
                    (kind, len(raw), hashlib.sha256(raw).hexdigest())
                    for kind, raw in inventory
                ],
            }
    except Exception as exc:
        raise DetectionImageProcessingError("Detection image metadata could not be read") from exc


def _metadata_matches(original: dict, candidate: dict) -> bool:
    if original["dimensions"] != candidate["dimensions"]:
        return False
    if original["orientation"] != candidate["orientation"]:
        return False
    if original["protected_segments"] != candidate["protected_segments"]:
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
    protected_segments = _jpeg_protected_segments(content)
    original_metadata = _metadata_snapshot(content, protected_segments)
    metadata_ms = (time.perf_counter() - metadata_started) * 1000
    compression_ms = 0.0
    try:
        with Image.open(BytesIO(content)) as source:
            source.load()
            for quality in _JPEG_QUALITIES:
                compression_started = time.perf_counter()
                output = BytesIO()
                options = {"format": "JPEG", "quality": quality, "optimize": True}
                source.save(output, **options)
                candidate = _inject_protected_segments(output.getvalue(), protected_segments)
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
