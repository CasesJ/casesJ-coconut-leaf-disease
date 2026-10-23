"""Resolve private image references and present first-party media paths only."""
from __future__ import annotations

import logging
import re
from copy import deepcopy
from pathlib import Path
from urllib.parse import unquote, urlsplit

from app.core import config
from app.infrastructure.cloudinary_storage import download_asset

logger = logging.getLogger(__name__)
_KINDS = {"original", "annotated"}
_FIREBASE_STORAGE_HOST = "firebasestorage.googleapis.com"
_LOCAL_PREFIXES = {
    "original": "/static/uploads/",
    "annotated": "/static/annotated_uploads/",
}


class MediaNotFound(LookupError):
    pass


class MediaUnavailable(RuntimeError):
    pass


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


def read_detection_image(record: dict, kind: str) -> tuple[bytes, str]:
    record_id = str(record.get("id") or record.get("record_id") or "")
    return _read_one(
        record, kind, record_id,
        url_field="annotated_image_url" if kind == "annotated" else "image_url",
        path_field="annotated_storage_path" if kind == "annotated" else "original_storage_path",
    )


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
    return read_detection_image(record, kind)


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
    if isinstance(assets, dict) and isinstance(assets.get(kind), dict):
        return True
    field = "annotated_image_url" if kind == "annotated" else "image_url"
    path_field = "annotated_storage_path" if kind == "annotated" else "original_storage_path"
    if record.get(field) or record.get(path_field):
        return True
    record_id = str(record.get("id") or "")
    return (_local_file(record.get("image_url" if kind == "original" else "annotated_image_url")
                        or record.get("original_storage_path" if kind == "original" else "annotated_storage_path"),
                        kind, record_id) is not None
            or _local_file(record.get("image_path"), kind, record_id, allow_leaf=False) is not None)


def present_detection_record(record: dict, *, expert: bool = False) -> dict:
    """Replace stored delivery values with authenticated first-party routes."""
    value = deepcopy(record)
    farmer_id = str(value.get("user_id") or "")
    record_id = str(value.get("id") or value.get("record_id") or "")
    base = (f"/expert/media/detections/{farmer_id}/{record_id}" if expert
            else f"/media/detections/{record_id}")
    value.pop("image_assets", None)
    value.pop("cloudinary_asset", None)
    value.pop("original_storage_path", None)
    value.pop("annotated_storage_path", None)
    if value.get("image_path"):
        value["image_path"] = Path(str(value["image_path"])).name
    for kind, field in (("original", "image_url"), ("annotated", "annotated_image_url")):
        if has_record_image(record, kind):
            value[field] = f"{base}/{kind}"
        else:
            value.pop(field, None)
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
    value.pop("image_assets", None)
    value.pop("original_storage_path", None)
    value.pop("annotated_storage_path", None)
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
        baseline.pop("image_assets", None)
        baseline["image_url"] = f"{prefix}/original"
        baseline["annotated_image_url"] = f"{prefix}/annotated"
    else:
        baseline.pop("image_url", None)
        baseline.pop("annotated_image_url", None)
    baseline.pop("image_assets", None)
    baseline.pop("original_storage_path", None)
    baseline.pop("annotated_storage_path", None)
    value["baseline"] = baseline
    value["case"] = present_monitoring_case(case)
    value["follow_ups"] = [present_monitoring_follow_up(record, expert=expert)
                            for record in value.get("follow_ups", [])]
    return value
