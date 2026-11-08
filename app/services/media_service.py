"""Resolve private image references and present first-party media paths only."""
from __future__ import annotations

import logging
import math
import re
import threading
import time
from concurrent.futures import Future
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import Image, ImageDraw, ImageFont, ImageOps

from app.core import config
from app.infrastructure.cloudinary_storage import delete_asset, download_asset, stream_asset, upload_asset

logger = logging.getLogger(__name__)
_KINDS = {"original", "annotated"}
_FIREBASE_STORAGE_HOST = "firebasestorage.googleapis.com"
_LOCAL_PREFIXES = {
    "original": "/static/uploads/",
    "annotated": "/static/annotated_uploads/",
}
_THUMBNAIL_LOCK = threading.Lock()
_INFLIGHT_THUMBNAILS: dict[tuple, dict] = {}


class MediaNotFound(LookupError):
    pass


class MediaUnavailable(RuntimeError):
    pass


def _thumbnail_key(record: dict) -> tuple:
    assets = record.get("image_assets") if isinstance(record.get("image_assets"), dict) else {}
    original = assets.get("original") if isinstance(assets.get("original"), dict) else {}
    return (
        str(record.get("user_id") or ""),
        str(record.get("id") or record.get("record_id") or ""),
        str(original.get("asset_id") or ""),
        str(original.get("version") or ""),
        str(original.get("sha256") or ""),
    )


def _is_retired_hybrid_raw_record(record: dict) -> bool:
    """Identify the fixed raw-source shape produced by the former worker."""
    assets = record.get("image_assets") if isinstance(record.get("image_assets"), dict) else {}
    original = assets.get("original") if isinstance(assets.get("original"), dict) else {}
    return (
        original.get("storage_representation") == "authenticated_raw_source"
        and original.get("provider") == "cloudinary"
        and original.get("resource_type") == "raw"
    )


def _firebase_location(value: object) -> tuple[str, str] | None:
    """Accept only Firebase Storage reference formats, never arbitrary URLs."""
    raw = str(value or "").strip()
    if not raw:
        return None
    parsed = urlsplit(raw)
    if parsed.scheme == "gs":
        bucket = parsed.netloc
        object_name = unquote(parsed.path.lstrip("/"))
        if bucket and object_name:
            return bucket, object_name
        return None
    if parsed.scheme != "https" or parsed.hostname != _FIREBASE_STORAGE_HOST:
        return None
    match = re.fullmatch(r"/v0/b/([^/]+)/o/(.+)", parsed.path)
    if not match:
        return None
    return match.group(1), unquote(match.group(2))


def _local_file(value: object, kind: str, record_id: str, *, allow_leaf: bool = True) -> Path | None:
    prefix = _LOCAL_PREFIXES[kind]
    root = config.UPLOAD_IMAGE_DIR if kind == "original" else config.ANNOTATED_IMAGE_DIR
    raw = str(value or "").strip()
    if raw:
        parsed = urlsplit(raw)
        path = unquote(parsed.path if parsed.scheme else raw).replace("\\", "/")
        raw_path = Path(path)
        if raw_path.is_absolute():
            try:
                candidate = raw_path.resolve()
                if candidate.parent == root.resolve() and candidate.is_file():
                    return candidate
            except OSError:
                pass
        marker = prefix.lstrip("/")
        normalized = path.lstrip("/")
        if normalized.startswith(marker):
            name = normalized[len(marker):]
            if "/" not in name and name not in {"", ".", ".."}:
                candidate = (root / name).resolve()
                if candidate.parent == root.resolve() and candidate.is_file():
                    return candidate
        # Older SQLite rows often stored just the leaf filename.
        if allow_leaf and "/" not in normalized and Path(normalized).name == normalized:
            candidate = (root / normalized).resolve()
            if candidate.parent == root.resolve() and candidate.is_file():
                return candidate
    if record_id:
        for suffix in (".jpg", ".jpeg", ".png"):
            candidate = (root / f"{record_id}{suffix}").resolve()
            if candidate.parent == root.resolve() and candidate.is_file():
                return candidate
    return None


def _content_type(reference: dict | None, fallback: object = None) -> str:
    value = (reference or {}).get("content_type") or fallback
    if value in {"image/jpeg", "image/png"}:
        return str(value)
    return "image/jpeg"


def _read_firebase_asset(reference: dict | None, url: object, storage_path: object) -> tuple[bytes, str]:
    location = _firebase_location(url)
    if location is None and storage_path:
        raw_path = str(storage_path)
        safe_prefix = raw_path.startswith(("detections/", "monitoring/", "monitoring_identity/"))
        if (safe_prefix and not raw_path.startswith(("/", "\\"))
                and ".." not in Path(raw_path).parts and "\\" not in raw_path):
            location = ("", raw_path)
    if not location:
        raise MediaNotFound("Historical Firebase image reference not found")

    try:
        from firebase_admin import storage  # noqa: PLC0415
        bucket = storage.bucket()
        bucket_name, object_name = location
        if bucket_name and bucket_name != bucket.name:
            raise MediaNotFound("Historical Firebase image reference not found")
        blob = bucket.blob(object_name)
        generation = (reference or {}).get("storage_generation")
        kwargs = {"end": config.MAX_IMAGE_UPLOAD_BYTES}
        if generation:
            kwargs["if_generation_match"] = int(generation)
        contents = blob.download_as_bytes(**kwargs)
        if len(contents) > config.MAX_IMAGE_UPLOAD_BYTES:
            raise MediaUnavailable("Stored image exceeds the configured limit")
        suffix = Path(object_name).suffix.lower()
        fallback_type = "image/png" if suffix == ".png" else "image/jpeg"
        return contents, _content_type(reference, fallback_type)
    except MediaNotFound:
        raise
    except Exception as error:
        logger.warning("Historical Firebase media read failed (%s)", type(error).__name__)
        raise MediaUnavailable("Historical Firebase image is temporarily unavailable") from None


def _read_one(record: dict, kind: str, record_id: str, *,
              url_field: str, path_field: str) -> tuple[bytes, str]:
    if kind not in _KINDS:
        raise MediaNotFound("Image not found")
    assets = record.get("image_assets")
    asset = assets.get(kind) if isinstance(assets, dict) else None
    if isinstance(asset, dict) and asset.get("provider") == "cloudinary":
        try:
            return download_asset(asset)
        except Exception as error:
            logger.warning("Cloudinary media read failed (%s)", type(error).__name__)
            raise MediaUnavailable("Image is temporarily unavailable") from None

    url = record.get(url_field)
    path = record.get(path_field)
    allowed_paths = _expected_legacy_storage_paths(record, kind, record_id)
    firebase_location = _firebase_location(url)
    if firebase_location:
        if firebase_location[1] not in allowed_paths:
            raise MediaNotFound("Historical Firebase image reference not found")
        return _read_firebase_asset(record, url, path)
    firebase_path = isinstance(path, str) and path.startswith(
        ("detections/", "monitoring/", "monitoring_identity/")
    )
    if firebase_path:
        if path not in allowed_paths:
            raise MediaNotFound("Historical Firebase image reference not found")
        return _read_firebase_asset(record, url, path)

    local = _local_file(url or path, kind, record_id)
    if local is None:
        local = _local_file(record.get("image_path"), kind, record_id, allow_leaf=False)
    if local is not None:
        try:
            if local.stat().st_size > config.MAX_IMAGE_UPLOAD_BYTES:
                raise MediaUnavailable("Stored image exceeds the configured limit")
            content = local.read_bytes()
            if len(content) > config.MAX_IMAGE_UPLOAD_BYTES:
                raise MediaUnavailable("Stored image exceeds the configured limit")
            content_type = "image/png" if local.suffix.lower() == ".png" else "image/jpeg"
            return content, content_type
        except MediaUnavailable:
            raise
        except OSError:
            raise MediaUnavailable("Historical local image is temporarily unavailable") from None
    raise MediaNotFound("Image not found")


def _generate_detection_thumbnail(record: dict) -> tuple[bytes, str]:
    """Generate one thumbnail per record/process and share it with waiters."""
    key = _thumbnail_key(record)
    with _THUMBNAIL_LOCK:
        state = _INFLIGHT_THUMBNAILS.get(key)
        if state is None:
            state = {"future": Future(), "persistence_claimed": False}
            _INFLIGHT_THUMBNAILS[key] = state
            leader = True
        else:
            leader = False
    future = state["future"]
    if not leader:
        return future.result()

    try:
        download_started = time.perf_counter()
        original, _ = _read_one(
            record, "original", str(record.get("id") or record.get("record_id") or ""),
            url_field="image_url", path_field="original_storage_path",
        )
        download_ms = (time.perf_counter() - download_started) * 1000
        generation_started = time.perf_counter()
        with Image.open(BytesIO(original)) as source:
            with ImageOps.exif_transpose(source).convert("RGB") as image:
                image.thumbnail((480, 480), Image.Resampling.LANCZOS)
                output = BytesIO()
                image.save(output, format="JPEG", quality=78, optimize=True)
                result = output.getvalue(), "image/jpeg"
        generation_ms = (time.perf_counter() - generation_started) * 1000
        logger.info(
            "Detection thumbnail generated record_id=%s original_download_ms=%.0f generation_ms=%.0f thumbnail_bytes=%d",
            key[1], download_ms, generation_ms, len(result[0]),
        )
        future.set_result(result)
        return result
    except BaseException as error:
        future.set_exception(error)
        with _THUMBNAIL_LOCK:
            if _INFLIGHT_THUMBNAILS.get(key) is state:
                del _INFLIGHT_THUMBNAILS[key]
        raise


def claim_detection_thumbnail_persistence(record: dict) -> bool:
    """Allow only one response to schedule persistence for shared generated bytes."""
    key = _thumbnail_key(record)
    with _THUMBNAIL_LOCK:
        state = _INFLIGHT_THUMBNAILS.get(key)
        if state is None or state["persistence_claimed"]:
            return False
        state["persistence_claimed"] = True
        return True


def read_detection_image(record: dict, kind: str) -> tuple[bytes, str]:
    if kind == "thumbnail":
        assets = record.get("image_assets")
        thumbnail = assets.get("thumbnail") if isinstance(assets, dict) else None
        if isinstance(thumbnail, dict) and thumbnail.get("provider") == "cloudinary":
            try:
                return download_asset(thumbnail)
            except Exception as error:
                logger.warning("Cloudinary thumbnail read failed (%s)", type(error).__name__)
                raise MediaUnavailable("Image preview is temporarily unavailable") from None
        if _is_retired_hybrid_raw_record(record):
            raise MediaUnavailable("Hybrid image preview is not available yet")
        try:
            return _generate_detection_thumbnail(record)
        except Exception as error:
            logger.warning("Detection thumbnail generation failed (%s)", type(error).__name__)
            raise MediaUnavailable("Image preview could not be generated") from None

    record_id = str(record.get("id") or record.get("record_id") or "")
    try:
        return _read_one(
            record, kind, record_id,
            url_field="annotated_image_url" if kind == "annotated" else "image_url",
            path_field="annotated_storage_path" if kind == "annotated" else "original_storage_path",
        )
    except MediaNotFound:
        if kind != "annotated":
            raise
        if _is_retired_hybrid_raw_record(record):
            raise MediaUnavailable("Hybrid annotation preview is not available yet") from None
        original, content_type = _read_one(
            record, "original", record_id,
            url_field="image_url", path_field="original_storage_path",
        )
        return _render_annotated_preview(record, original), "image/jpeg"


def stream_detection_original(record: dict):
    """Return a lazy stream only for retired hybrid raw originals."""
    if not _is_retired_hybrid_raw_record(record):
        return None
    assets = record.get("image_assets") or {}
    original = assets.get("original")
    try:
        return stream_asset(original), _content_type(original)
    except Exception as error:
        logger.warning("Cloudinary retired-hybrid raw stream failed (%s)", type(error).__name__)
        raise MediaUnavailable("Image is temporarily unavailable") from None


def persist_detection_thumbnail(record: dict, thumbnail_bytes: bytes) -> None:
    """Cache a legacy record's generated preview as a private asset for later visits."""
    assets = record.get("image_assets")
    if not isinstance(assets, dict) or not isinstance(assets.get("original"), dict):
        return
    if assets.get("thumbnail") or assets["original"].get("provider") != "cloudinary":
        return
    farmer_id = str(record.get("user_id") or "").strip()
    record_id = str(record.get("id") or record.get("record_id") or "").strip()
    if not farmer_id or not record_id or not thumbnail_bytes:
        return
    key = _thumbnail_key(record)
    thumbnail = None
    try:
        from app.services import detection_service  # noqa: PLC0415

        current = detection_service.find_canonical_record_for_user(farmer_id, record_id)
        current_assets = (current.get("image_assets")
                          if isinstance(current, dict) and isinstance(current.get("image_assets"), dict)
                          else {})
        if isinstance(current_assets.get("thumbnail"), dict) and current_assets["thumbnail"]:
            return
        upload_started = time.perf_counter()
        thumbnail = upload_asset(thumbnail_bytes, "image/jpeg", "detection-thumbnail")
        upload_ms = (time.perf_counter() - upload_started) * 1000
        persistence_started = time.perf_counter()
        published, existing = detection_service.publish_thumbnail_if_absent(
            farmer_id, record_id, thumbnail,
        )
        persistence_ms = (time.perf_counter() - persistence_started) * 1000
        logger.info(
            "Detection thumbnail persisted record_id=%s upload_ms=%.0f firestore_ms=%.0f published=%s existing=%s",
            record_id, upload_ms, persistence_ms, published, bool(existing),
        )
        if not published:
            delete_asset(thumbnail)
    except Exception as error:
        if thumbnail is not None:
            # A transaction can commit while its response is lost. Delete only
            # after a canonical read proves this exact reference was not published.
            try:
                from app.services import detection_service  # noqa: PLC0415
                current = detection_service.find_canonical_record_for_user(farmer_id, record_id)
                current_assets = (current.get("image_assets")
                                  if isinstance(current, dict)
                                  and isinstance(current.get("image_assets"), dict) else {})
                if current_assets.get("thumbnail") != thumbnail:
                    delete_asset(thumbnail)
            except Exception:
                pass
        logger.warning("Legacy detection thumbnail could not be cached (%s)", type(error).__name__)
    finally:
        with _THUMBNAIL_LOCK:
            state = _INFLIGHT_THUMBNAILS.get(key)
            if state is not None and state.get("persistence_claimed"):
                del _INFLIGHT_THUMBNAILS[key]


def _render_annotated_preview(record: dict, original: bytes) -> bytes:
    """Build a temporary annotated image for legacy URL consumers and downloads."""
    try:
        with Image.open(BytesIO(original)) as source:
            with ImageOps.exif_transpose(source).convert("RGB") as oriented:
                image = oriented.copy()
            image.load()
    except Exception:
        raise MediaUnavailable("Original image cannot be annotated") from None

    width, height = image.size
    draw = ImageDraw.Draw(image)
    detections = record.get("detections") or record.get("inference_results") or record.get("predictions") or []
    if isinstance(detections, str):
        try:
            import json
            detections = json.loads(detections)
        except Exception:
            detections = []
    if not isinstance(detections, list):
        detections = []
    font = ImageFont.load_default()
    thickness = max(2, round(min(width, height) * 0.004))
    for detection in detections:
        if not isinstance(detection, dict):
            continue
        normalized = detection.get("bbox_normalized")
        coordinates = normalized if isinstance(normalized, (list, tuple)) and len(normalized) == 4 else detection.get("bbox")
        if not isinstance(coordinates, (list, tuple)) or len(coordinates) != 4:
            continue
        try:
            values = [float(value) for value in coordinates]
            if not all(math.isfinite(value) for value in values):
                continue
            if coordinates is normalized:
                values = [value * (width if index % 2 == 0 else height)
                          for index, value in enumerate(values)]
            else:
                source_size = record.get("image_dimensions") or record.get("original_image_dimensions") or {}
                source_width = float(source_size.get("width") or source_size.get("image_width") or width)
                source_height = float(source_size.get("height") or source_size.get("image_height") or height)
                if source_width > 0 and source_height > 0:
                    values = [value * (width if index % 2 == 0 else height)
                              / (source_width if index % 2 == 0 else source_height)
                              for index, value in enumerate(values)]
            x1, y1, x2, y2 = values
            x1, x2 = sorted((max(0, min(width - 1, round(x1))), max(0, min(width - 1, round(x2)))))
            y1, y2 = sorted((max(0, min(height - 1, round(y1))), max(0, min(height - 1, round(y2)))))
            if x2 <= x1 or y2 <= y1:
                continue
            color = (226, 67, 58)
            for offset in range(thickness):
                draw.rectangle((x1 + offset, y1 + offset, x2 - offset, y2 - offset),
                               outline=color)
            label = str(detection.get("class") or detection.get("label") or "Detection")[:100]
            try:
                confidence = float(detection.get("confidence"))
                if math.isfinite(confidence):
                    label += f" {round(confidence * 100)}%"
            except (TypeError, ValueError, OverflowError):
                pass
            left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
            label_width, label_height = right - left, bottom - top
            label_y = max(0, y1 - label_height - 8)
            draw.rectangle((x1, label_y, min(width, x1 + label_width + 8),
                            min(height, label_y + label_height + 6)), fill=color)
            draw.text((x1 + 4, label_y + 2), label, fill=(255, 255, 255), font=font)
        except (TypeError, ValueError, OverflowError):
            continue

    output = BytesIO()
    image.save(output, format="JPEG", quality=90, optimize=True)
    return output.getvalue()


def _expected_legacy_storage_paths(record: dict, kind: str, record_id: str) -> set[str]:
    suffixes = ("jpg", "jpeg", "png")
    file_kinds = (kind, "original" if kind == "original" else "annotated")
    paths = set()
    scope = record.get("_media_scope")
    if scope == "monitoring" or (scope != "detection" and record.get("case_id")
                                  and record.get("farmer_id") and record.get("id")):
        farmer_id = str(record.get("farmer_id") or "")
        case_id = str(record.get("case_id") or "")
        follow_up_id = str(record.get("id") or "")
        if all(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value)
               for value in (farmer_id, case_id, follow_up_id)):
            for file_kind in file_kinds:
                for suffix in suffixes:
                    paths.add(f"monitoring/{farmer_id}/{case_id}/{follow_up_id}/{file_kind}.{suffix}")
    else:
        safe_id = str(record_id or record.get("detection_id") or "")
        if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", safe_id):
            for file_kind in file_kinds:
                for suffix in suffixes:
                    paths.add(f"detections/{safe_id}/{file_kind}.{suffix}")
    return paths


def read_monitoring_image(record: dict, kind: str) -> tuple[bytes, str]:
    value = dict(record)
    if not value.get("detections") and isinstance(value.get("predictions"), list):
        value["detections"] = value["predictions"]
    return read_detection_image(value, kind)


def read_tree_reference(reference: dict) -> tuple[bytes, str]:
    asset = reference.get("image_asset")
    if isinstance(asset, dict) and asset.get("provider") == "cloudinary":
        try:
            return download_asset(asset)
        except Exception:
            raise MediaUnavailable("Whole-tree image is temporarily unavailable") from None
    try:
        from app.infrastructure.firebase.cloud_storage import read_private_tree_reference
        contents = read_private_tree_reference(reference)
        if len(contents) > config.MAX_IMAGE_UPLOAD_BYTES:
            raise MediaUnavailable("Stored image exceeds the configured limit")
        return contents, _content_type(reference)
    except MediaUnavailable:
        raise
    except Exception:
        raise MediaUnavailable("Whole-tree image is temporarily unavailable") from None


def has_record_image(record: dict, kind: str) -> bool:
    assets = record.get("image_assets")
    asset = assets.get(kind) if isinstance(assets, dict) else None
    if isinstance(asset, dict) and asset.get("provider") == "cloudinary":
        return True
    field = "annotated_image_url" if kind == "annotated" else "image_url"
    path_field = "annotated_storage_path" if kind == "annotated" else "original_storage_path"
    record_id = str(record.get("id") or record.get("record_id") or "")
    allowed_paths = _expected_legacy_storage_paths(record, kind, record_id)
    url = record.get(field)
    firebase_location = _firebase_location(url)
    if firebase_location and firebase_location[1] in allowed_paths:
        return True
    storage_path = record.get(path_field)
    if (isinstance(storage_path, str)
            and storage_path.startswith(("detections/", "monitoring/", "monitoring_identity/"))
            and storage_path in allowed_paths):
        return True
    available = (_local_file(url or storage_path, kind, record_id) is not None
                 or _local_file(record.get("image_path"), kind, record_id, allow_leaf=False) is not None)
    if available:
        return True
    if kind == "annotated" and _is_retired_hybrid_raw_record(record):
        return False
    if kind == "annotated":
        return has_record_image(record, "original")
    return False


def _strip_image_storage_fields(value: dict) -> None:
    """Remove shared provider retrieval details from a presentation copy."""
    for field in ("image_assets", "original_storage_path", "annotated_storage_path"):
        value.pop(field, None)


def present_detection_record(record: dict, *, expert: bool = False) -> dict:
    """Replace stored delivery values with authenticated first-party routes."""
    value = deepcopy(record)
    farmer_id = str(value.get("user_id") or "")
    record_id = str(value.get("id") or value.get("record_id") or "")
    base = (f"/expert/media/detections/{farmer_id}/{record_id}" if expert
            else f"/media/detections/{record_id}")
    _strip_image_storage_fields(value)
    value.pop("cloudinary_asset", None)
    if value.get("image_path"):
        value["image_path"] = Path(str(value["image_path"])).name
    for kind, field in (("original", "image_url"), ("annotated", "annotated_image_url")):
        if has_record_image(record, kind):
            value[field] = f"{base}/{kind}"
        else:
            value.pop(field, None)
    if has_record_image(record, "original") and (
            not _is_retired_hybrid_raw_record(record)
            or isinstance((record.get("image_assets") or {}).get("thumbnail"), dict)):
        value["thumbnail_url"] = f"{base}/thumbnail"
    else:
        value.pop("thumbnail_url", None)
    return value


def present_tree_reference(reference: dict | None) -> dict | None:
    if not isinstance(reference, dict):
        return reference
    value = deepcopy(reference)
    value.pop("image_asset", None)
    value.pop("image_assets", None)
    # Old provider URLs and object paths are server-side retrieval details.
    value.pop("image_url", None)
    value.pop("storage_path", None)
    value.pop("storage_generation", None)
    return value


def present_monitoring_follow_up(record: dict, *, expert: bool = False) -> dict:
    value = deepcopy(record)
    farmer_id = str(value.get("farmer_id") or "")
    case_id = str(value.get("case_id") or "")
    follow_up_id = str(value.get("id") or "")
    base = (f"/expert/media/monitoring/{farmer_id}/{case_id}/follow-ups/{follow_up_id}"
            if expert else f"/media/monitoring/{case_id}/follow-ups/{follow_up_id}")
    assets = value.get("image_assets") if isinstance(value.get("image_assets"), dict) else {}
    for kind, field in (("original", "image_url"), ("annotated", "annotated_image_url")):
        if assets.get(kind) or value.get(field) or value.get(
            "annotated_storage_path" if kind == "annotated" else "original_storage_path"
        ):
            value[field] = f"{base}/{kind}"
        else:
            value.pop(field, None)
    _strip_image_storage_fields(value)
    if value.get("tree_reference"):
        value["tree_reference"] = present_tree_reference(value["tree_reference"])
    return value


def present_monitoring_case(case: dict) -> dict:
    value = deepcopy(case)
    value.pop("baseline_image_reference", None)
    if value.get("tree_reference"):
        value["tree_reference"] = present_tree_reference(value["tree_reference"])
    return value


def present_monitoring_overview(overview: dict, *, expert: bool = False) -> dict:
    value = deepcopy(overview)
    case = value.get("case") or {}
    case_id = str(case.get("id") or "")
    farmer_id = str(case.get("farmer_id") or "")
    baseline = value.get("baseline") or {}
    snapshot = case.get("baseline_image_reference")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    baseline_record = {
        **baseline,
        "image_assets": baseline.get("image_assets") or snapshot.get("image_assets"),
        "original_storage_path": baseline.get("original_storage_path") or snapshot.get("original_storage_path"),
        "annotated_storage_path": baseline.get("annotated_storage_path") or snapshot.get("annotated_storage_path"),
    }
    # Legacy case snapshots may contain the provider URL while the overview
    # suppresses the raw reference; first-party endpoints resolve it by owner.
    if (baseline.get("image_url") or baseline.get("annotated_image_url")
            or baseline_record.get("image_assets") or baseline_record.get("original_storage_path")
            or baseline_record.get("annotated_storage_path")):
        prefix = (f"/expert/media/monitoring/{farmer_id}/{case_id}/baseline"
                  if expert else f"/media/monitoring/{case_id}/baseline")
        baseline["image_url"] = f"{prefix}/original"
        baseline["annotated_image_url"] = f"{prefix}/annotated"
    else:
        baseline.pop("image_url", None)
        baseline.pop("annotated_image_url", None)
    _strip_image_storage_fields(baseline)
    value["baseline"] = baseline
    value["case"] = present_monitoring_case(case)
    value["follow_ups"] = [present_monitoring_follow_up(record, expert=expert)
                            for record in value.get("follow_ups", [])]
    return value
