"""Image encoding and durable evidence upload helpers."""
from __future__ import annotations

from io import BytesIO
import hashlib
import logging
import time

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from app.core import config
from app.core.resource_metrics import process_memory_metrics
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
_DETECTION_THUMBNAIL_SIZE = (480, 480)
_DETECTION_THUMBNAIL_QUALITY = 78


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
            # Camera and browser encoders can append non-image padding after
            # EOI. JPEG decoders ignore it, and it cannot affect the pixels or
            # protected metadata already parsed above.  Treat EOI as the end
            # of the JPEG instead of rejecting an otherwise valid upload.
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


def _metadata_matches_resized(original: dict, candidate: dict) -> bool:
    """Verify all protected evidence metadata while allowing a storage derivative."""
    return (original["orientation"] == candidate["orientation"]
            and original["protected_segments"] == candidate["protected_segments"])


def _log_memory(stage: str, operation_id: str) -> None:
    metrics = process_memory_metrics()
    logger.info(
        "Image storage memory operation_id=%s stage=%s rss_mib=%s cgroup_mib=%s cgroup_limit_mib=%s oom_kill=%s",
        operation_id, stage,
        round(metrics["rss_bytes"] / (1024 * 1024), 1) if metrics["rss_bytes"] else "unavailable",
        round(metrics["cgroup_current_bytes"] / (1024 * 1024), 1) if metrics["cgroup_current_bytes"] else "unavailable",
        round(metrics["cgroup_limit_bytes"] / (1024 * 1024), 1) if metrics["cgroup_limit_bytes"] else "unavailable",
        metrics["cgroup_oom_kill"] if metrics["cgroup_oom_kill"] is not None else "unavailable",
    )


def _storage_draft_factor(width: int, height: int, max_pixels: int) -> int | None:
    """Choose a libjpeg power-of-two DCT reduction that is at or below the cap."""
    pixels = width * height
    if pixels <= max_pixels:
        return None
    factor = 2
    while (width * height) / (factor * factor) > max_pixels:
        factor *= 2
    return factor


def _storage_draft_size(width: int, height: int, max_pixels: int) -> tuple[int, int] | None:
    factor = _storage_draft_factor(width, height, max_pixels)
    if factor is None:
        return None
    return max(1, (width + factor - 1) // factor), max(1, (height + factor - 1) // factor)


def _opencv_reduced_flag(factor: int) -> int:
    return {
        2: cv2.IMREAD_REDUCED_COLOR_2,
        4: cv2.IMREAD_REDUCED_COLOR_4,
        8: cv2.IMREAD_REDUCED_COLOR_8,
    }[factor] | cv2.IMREAD_IGNORE_ORIENTATION


def _prepare_cloudinary_copy(
    content: bytes,
    content_type: str,
    timings: dict[str, float] | None = None,
    operation_id: str = "unknown",
) -> tuple[bytes, str]:
    """Return a verified, bounded storage copy without changing inference evidence."""
    if content_type != "image/jpeg":
        if len(content) <= config.DETECTION_STORAGE_MAX_BYTES:
            return content, "source_original"
        raise DetectionImageProcessingError(
            "Only oversized JPEG detection images can be compressed for storage"
        )

    metadata_started = time.perf_counter()
    protected_segments = _jpeg_protected_segments(content)
    original_metadata = _metadata_snapshot(content, protected_segments)
    metadata_ms = (time.perf_counter() - metadata_started) * 1000
    source_width, source_height = original_metadata["dimensions"]
    storage_pixel_limit = min(config.CLOUDINARY_MAX_IMAGE_PIXELS,
                              config.DETECTION_STORAGE_MAX_PIXELS)
    draft_factor = _storage_draft_factor(source_width, source_height, storage_pixel_limit)
    draft_size = _storage_draft_size(source_width, source_height, storage_pixel_limit)
    if len(content) <= config.DETECTION_STORAGE_MAX_BYTES and draft_size is None:
        return content, "source_original"
    compression_ms = 0.0
    rss_samples: list[float] = []

    def sample_memory(stage: str) -> None:
        _log_memory(stage, operation_id)
        rss_bytes = process_memory_metrics()["rss_bytes"]
        if rss_bytes is not None:
            rss_samples.append(rss_bytes / (1024 * 1024))

    def record_peak() -> None:
        if timings is not None and rss_samples:
            timings["storage_preparation_peak_rss_mib"] = max(rss_samples)

    try:
        with Image.open(BytesIO(content)) as source:
            if draft_size is not None:
                # Must precede load(): libjpeg can then decode only the selected
                # DCT raster instead of allocating the full DJI image.
                source.draft("RGB", draft_size)
                if source.size[0] * source.size[1] > storage_pixel_limit:
                    # This JPEG ignores Pillow's draft request. Do not load its
                    # full raster: OpenCV can perform the same decoder-level
                    # reduction while retaining the protected metadata below.
                    sample_memory("before_opencv_reduced_decode")
                    raster = cv2.imdecode(
                        np.frombuffer(content, np.uint8), _opencv_reduced_flag(draft_factor),
                    )
                    sample_memory("after_opencv_reduced_decode")
                    if raster is None or raster.shape[0] * raster.shape[1] > storage_pixel_limit:
                        raise DetectionImageProcessingError(
                            "JPEG cannot be safely reduced within the storage pixel limit"
                        )
                    for quality in _JPEG_QUALITIES:
                        compression_started = time.perf_counter()
                        sample_memory(f"before_opencv_jpeg_encode_q{quality}")
                        encoded, output = cv2.imencode(
                            ".jpg", raster, [cv2.IMWRITE_JPEG_QUALITY, quality],
                        )
                        sample_memory(f"after_opencv_jpeg_encode_q{quality}")
                        compression_ms += (time.perf_counter() - compression_started) * 1000
                        if not encoded:
                            continue
                        candidate = _inject_protected_segments(output.tobytes(), protected_segments)
                        if len(candidate) > config.DETECTION_STORAGE_MAX_BYTES:
                            continue
                        metadata_started = time.perf_counter()
                        sample_memory("before_storage_metadata_verification")
                        candidate_metadata = _metadata_snapshot(candidate)
                        metadata_ms += (time.perf_counter() - metadata_started) * 1000
                        sample_memory("after_storage_metadata_verification")
                        if _metadata_matches_resized(original_metadata, candidate_metadata):
                            if timings is not None:
                                timings["image_compression_ms"] = compression_ms
                                timings["image_metadata_verification_ms"] = metadata_ms
                            record_peak()
                            return candidate, "reduced_jpeg_storage_copy"
                    raise DetectionImageProcessingError(
                        "JPEG cannot fit the storage byte limit while preserving required metadata"
                    )
            sample_memory("before_pillow_load")
            source.load()
            sample_memory("after_pillow_load")
            stored_dimensions = source.size
            if stored_dimensions[0] * stored_dimensions[1] > storage_pixel_limit:
                raise DetectionImageProcessingError(
                    "JPEG cannot be safely reduced within the storage pixel limit"
                )
            for quality in _JPEG_QUALITIES:
                compression_started = time.perf_counter()
                sample_memory(f"before_jpeg_encode_q{quality}")
                output = BytesIO()
                options = {"format": "JPEG", "quality": quality, "optimize": True}
                source.save(output, **options)
                candidate = _inject_protected_segments(output.getvalue(), protected_segments)
                output.close()
                compression_ms += (time.perf_counter() - compression_started) * 1000
                sample_memory(f"after_jpeg_encode_q{quality}")
                if len(candidate) > config.DETECTION_STORAGE_MAX_BYTES:
                    del candidate
                    continue
                metadata_started = time.perf_counter()
                sample_memory("before_storage_metadata_verification")
                candidate_metadata = _metadata_snapshot(candidate)
                metadata_ms += (time.perf_counter() - metadata_started) * 1000
                sample_memory("after_storage_metadata_verification")
                matches = (_metadata_matches_resized(original_metadata, candidate_metadata)
                           if draft_size is not None else _metadata_matches(original_metadata, candidate_metadata))
                if not matches:
                    # A lower quality changes only pixels, but encoder behavior
                    # can affect container metadata. Keep searching; never upload
                    # a candidate that failed verification.
                    del candidate
                    continue
                if timings is not None:
                    timings["image_compression_ms"] = compression_ms
                    timings["image_metadata_verification_ms"] = metadata_ms
                record_peak()
                return candidate, ("reduced_jpeg_storage_copy" if draft_size is not None
                                   else "compressed_jpeg_storage_copy")
    except DetectionImageProcessingError:
        raise
    except Exception as exc:
        raise DetectionImageProcessingError("Detection image compression failed") from exc
    raise DetectionImageProcessingError("JPEG cannot fit the configured storage limit while preserving required metadata")


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


def _detection_thumbnail(original_bytes: bytes) -> bytes:
    """Create a display-only JPEG without changing the stored evidence image."""
    with Image.open(BytesIO(original_bytes)) as source:
        # JPEG draft decoding lets Pillow read an 8x smaller DCT raster for a
        # 480px thumbnail instead of materialising a 48 MP source image. It is
        # intentionally used only for the disposable display thumbnail; the
        # original Cloudinary evidence bytes and their EXIF/XMP stay untouched.
        if source.format == "JPEG":
            source.draft("RGB", _DETECTION_THUMBNAIL_SIZE)
        with ImageOps.exif_transpose(source).convert("RGB") as image:
            image.thumbnail(_DETECTION_THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(
                output, format="JPEG", quality=_DETECTION_THUMBNAIL_QUALITY,
                optimize=True,
            )
            return output.getvalue()


def save_upload_image_assets(
    record_id: str,
    original_bytes: bytes,
    annotated_image: np.ndarray | None = None,
    timings: dict[str, float] | None = None,
    operation_id: str = "unknown",
) -> dict:
    """Upload the byte-identical source image and a small display thumbnail.

    The original preserves image quality, EXIF/XMP metadata, and the exact
    evidence bytes used by the detector. The thumbnail is intentionally
    metadata-free and display-only, so it can be fetched by record lists without
    downloading the original. Annotation previews are rendered from Firestore
    metadata in the client.
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
            original_bytes, content_type, timings=preparation_timings, operation_id=operation_id,
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
    _log_memory("before_cloudinary_upload", operation_id)
    category = ("detection-original" if representation == "source_original"
                else "detection-storage-copy")
    original = upload_asset(
        storage_bytes, content_type, category, operation_id=operation_id,
        source_bytes=len(original_bytes),
    )
    upload_ms = (time.perf_counter() - upload_started) * 1000
    _log_memory("after_cloudinary_upload", operation_id)
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
    try:
        with Image.open(BytesIO(storage_bytes)) as stored_image:
            original["storage_dimensions"] = {
                "width": int(stored_image.width), "height": int(stored_image.height),
            }
    except (UnidentifiedImageError, OSError):
        pass
    assets = {"original": original}

    # The thumbnail is only a list-preview optimization. It must never block a
    # valid disease record or replace the original, metadata-preserving evidence.
    thumbnail_started = time.perf_counter()
    try:
        thumbnail_bytes = _detection_thumbnail(original_bytes)
        preparation_timings["thumbnail_generation_ms"] = (
            time.perf_counter() - thumbnail_started
        ) * 1000
        thumbnail_upload_started = time.perf_counter()
        assets["thumbnail"] = upload_asset(
            thumbnail_bytes, "image/jpeg", "detection-thumbnail",
            operation_id=operation_id, source_bytes=len(thumbnail_bytes),
        )
        preparation_timings["thumbnail_cloudinary_upload_ms"] = (
            time.perf_counter() - thumbnail_upload_started
        ) * 1000
    except Exception as error:  # Preview failures are non-fatal by design.
        preparation_timings["thumbnail_generation_ms"] = (
            time.perf_counter() - thumbnail_started
        ) * 1000
        logger.warning(
            "Detection thumbnail preparation skipped operation_id=%s error_type=%s",
            operation_id, type(error).__name__,
        )

    logger.info(
        "Detection image prepared operation_id=%s source_bytes=%d stored_bytes=%d representation=%s compression_ms=%.0f metadata_verification_ms=%.0f",
        operation_id, len(original_bytes), len(storage_bytes), representation,
        preparation_timings.get("image_compression_ms", 0.0),
        preparation_timings.get("image_metadata_verification_ms", 0.0),
    )
    return {"image_assets": assets}
