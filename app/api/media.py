"""Firebase-authenticated, owner-checked delivery for all evidence images."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse

from app.core.security import verify_verified_firebase_token
from app.domain.expert import is_authorized_expert_identity
from app.services import detection_service
from app.services import media_service
from app.services import monitoring_service

router = APIRouter(tags=["private-media"])
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _uid(decoded: dict) -> str:
    uid = str(decoded.get("uid") or "").strip()
    if not _SAFE_ID.fullmatch(uid) or decoded.get("token_verified_offline"):
        raise HTTPException(401, "A verified Firebase identity is required")
    return uid


def _is_expert(decoded: dict) -> bool:
    # Preserve the wrapper's historical failure for a missing identity.
    if decoded is None:
        raise AttributeError("'NoneType' object has no attribute 'get'")
    return is_authorized_expert_identity(decoded)


def _farmer(decoded: dict) -> str:
    uid = _uid(decoded)
    if _is_expert(decoded):
        raise HTTPException(403, "Farmer access required")
    return uid


def _expert(decoded: dict) -> str:
    uid = _uid(decoded)
    if not _is_expert(decoded):
        raise HTTPException(403, "Expert access required")
    return uid


def _serve(contents: bytes, content_type: str) -> Response:
    if content_type not in {"image/jpeg", "image/png"}:
        raise HTTPException(415, "Unsupported stored image type")
    return Response(contents, media_type=content_type, headers={
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": "inline",
        "Vary": "Authorization",
    })


def _read_or_http_error(read, after_read=None) -> Response:
    try:
        contents, content_type = read()
        if after_read:
            after_read(contents)
        return _serve(contents, content_type)
    except media_service.MediaNotFound:
        raise HTTPException(404, "Image not found") from None
    except media_service.MediaUnavailable:
        raise HTTPException(503, "Image is temporarily unavailable") from None


def _stream_or_read_detection(record: dict, kind: str, after_read=None,
                              *, monitoring: bool = False) -> Response:
    if kind == "original":
        streamed = media_service.stream_detection_original(record)
        if streamed is not None:
            chunks, content_type = streamed
            return StreamingResponse(chunks, media_type=content_type, headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": "inline",
                "Vary": "Authorization",
            })
    read = media_service.read_monitoring_image if monitoring else media_service.read_detection_image
    return _read_or_http_error(lambda: read(record, kind), after_read)


def _detection_record(farmer_id: str, record_id: str) -> dict:
    if not _SAFE_ID.fullmatch(farmer_id) or not _SAFE_ID.fullmatch(record_id):
        raise HTTPException(404, "Image not found")
    record = detection_service.find_canonical_record_for_user(farmer_id, record_id)
    if (not record or str(record.get("user_id") or "") != farmer_id
            or str(record.get("id") or record.get("record_id") or record_id) != record_id):
        raise HTTPException(404, "Image not found")
    return record


@router.get("/media/detections/{record_id}/{kind}")
def farmer_detection_image(record_id: str, kind: str, background_tasks: BackgroundTasks,
                           decoded: dict = Depends(verify_verified_firebase_token)):
    farmer_id = _farmer(decoded)
    if kind not in {"original", "annotated", "thumbnail"}:
        raise HTTPException(404, "Image not found")
    record = _detection_record(farmer_id, record_id)
    assets = record.get("image_assets") if isinstance(record.get("image_assets"), dict) else {}
    should_cache_thumbnail = kind == "thumbnail" and not assets.get("thumbnail")
    def after_read(contents):
        if (should_cache_thumbnail
                and media_service.claim_detection_thumbnail_persistence(record)):
            background_tasks.add_task(media_service.persist_detection_thumbnail, record, contents)
    return _stream_or_read_detection(record, kind, after_read)


@router.get("/expert/media/detections/{farmer_id}/{record_id}/{kind}")
def expert_detection_image(farmer_id: str, record_id: str, kind: str, background_tasks: BackgroundTasks,
                           decoded: dict = Depends(verify_verified_firebase_token)):
    _expert(decoded)
    if kind not in {"original", "annotated", "thumbnail"} or not farmer_id or not record_id:
        raise HTTPException(404, "Image not found")
    record = _detection_record(farmer_id, record_id)
    assets = record.get("image_assets") if isinstance(record.get("image_assets"), dict) else {}
    should_cache_thumbnail = kind == "thumbnail" and not assets.get("thumbnail")
    def after_read(contents):
        if (should_cache_thumbnail
                and media_service.claim_detection_thumbnail_persistence(record)):
            background_tasks.add_task(media_service.persist_detection_thumbnail, record, contents)
    return _stream_or_read_detection(record, kind, after_read)


def _monitoring_image(farmer_id: str, case_id: str, follow_up_id: str | None,
                      kind: str, baseline: bool = False) -> Response:
    if (kind not in ({"original", "annotated", "thumbnail"} if baseline else {"original", "annotated", "thumbnail"})
            or not _SAFE_ID.fullmatch(farmer_id)
            or not _SAFE_ID.fullmatch(case_id)
            or (follow_up_id is not None and not _SAFE_ID.fullmatch(follow_up_id))):
        raise HTTPException(404, "Image not found")
    case = monitoring_service.get_case(farmer_id, case_id)
    if not case or str(case.get("farmer_id") or "") != farmer_id:
        raise HTTPException(404, "Monitoring evidence not found")
    if baseline:
        snapshot = case.get("baseline_image_reference")
        record = dict(snapshot) if isinstance(snapshot, dict) else {}
        record["id"] = case.get("baseline_detection_id")
        record["user_id"] = farmer_id
        record["_media_scope"] = "detection"
        if kind == "thumbnail":
            # The live detection owns the durable preview.  Prefer it over
            # the immutable case snapshot so monitoring reuses the same small
            # authenticated asset as Detection Records.
            try:
                record = dict(_detection_record(farmer_id, str(case.get("baseline_detection_id") or "")))
                record["_media_scope"] = "detection"
            except HTTPException:
                # Legacy cases can outlive their detection record.  Preserve
                # their existing on-demand thumbnail fallback from the snapshot.
                pass
        # Older snapshots sometimes contain only a first-party delivery URL,
        # not a durable provider reference. Resolve the exact owned detection
        # without loading treatment records, schedules, and all follow-ups.
        if not media_service.has_record_image(record, kind):
            record = dict(_detection_record(farmer_id, str(case.get("baseline_detection_id") or "")))
            record["_media_scope"] = "detection"
        else:
            record["predictions"] = case.get("baseline_prediction_snapshot") or []
        return _stream_or_read_detection(record, kind, monitoring=True)
    if not follow_up_id:
        raise HTTPException(404, "Monitoring evidence not found")
    record = monitoring_service.get_follow_up(farmer_id, case_id, follow_up_id)
    if (not record or record.get("farmer_id") != farmer_id
            or record.get("case_id") != case_id or record.get("id") != follow_up_id):
        raise HTTPException(404, "Monitoring evidence not found")
    record["_media_scope"] = "monitoring"
    return _stream_or_read_detection(record, kind, monitoring=True)


@router.get("/media/monitoring/{case_id}/baseline/{kind}")
def farmer_baseline_image(case_id: str, kind: str,
                          decoded: dict = Depends(verify_verified_firebase_token)):
    return _monitoring_image(_farmer(decoded), case_id, None, kind, baseline=True)


@router.get("/media/monitoring/{case_id}/follow-ups/{follow_up_id}/{kind}")
def farmer_follow_up_image(case_id: str, follow_up_id: str, kind: str,
                           decoded: dict = Depends(verify_verified_firebase_token)):
    return _monitoring_image(_farmer(decoded), case_id, follow_up_id, kind)


@router.get("/expert/media/monitoring/{farmer_id}/{case_id}/baseline/{kind}")
def expert_baseline_image(farmer_id: str, case_id: str, kind: str,
                          decoded: dict = Depends(verify_verified_firebase_token)):
    _expert(decoded)
    return _monitoring_image(farmer_id, case_id, None, kind, baseline=True)


@router.get("/expert/media/monitoring/{farmer_id}/{case_id}/follow-ups/{follow_up_id}/{kind}")
def expert_follow_up_image(farmer_id: str, case_id: str, follow_up_id: str, kind: str,
                           decoded: dict = Depends(verify_verified_firebase_token)):
    _expert(decoded)
    return _monitoring_image(farmer_id, case_id, follow_up_id, kind)


def _legacy_static_image(filename: str, kind: str, decoded: dict) -> Response:
    """Guard historic static URLs so cached/bookmarked paths cannot bypass auth."""
    uid = _uid(decoded)
    expert = _is_expert(decoded)
    safe_name = unquote(filename)
    if Path(safe_name).name != safe_name or safe_name in {"", ".", ".."}:
        raise HTTPException(404, "Image not found")
    path = ("/static/uploads/" if kind == "original" else "/static/annotated_uploads/") + safe_name
    records = detection_service.get_all_user_records(limit=2_000)
    matches = []
    for record in records:
        owner = str(record.get("user_id") or "")
        if not expert and owner != uid:
            continue
        field = "image_url" if kind == "original" else "annotated_image_url"
        candidate = str(record.get(field) or "")
        candidate_path = urlsplit(candidate).path if candidate else ""
        matches_path = candidate_path == path or Path(candidate_path).name == safe_name
        matches_id = str(record.get("id") or "") == Path(safe_name).stem
        if matches_path or matches_id:
            matches.append(record)
    if not matches:
        raise HTTPException(404, "Image not found")
    # Filenames are globally unique in the legacy directories; fail closed if
    # inconsistent historical records claim the same asset across farmers.
    owners = {str(record.get("user_id") or "") for record in matches}
    if len(owners) != 1:
        raise HTTPException(404, "Image not found")
    return _stream_or_read_detection(matches[0], kind)


@router.get("/static/uploads/{filename:path}")
def protected_legacy_original(filename: str,
                              decoded: dict = Depends(verify_verified_firebase_token)):
    return _legacy_static_image(filename, "original", decoded)


@router.get("/static/annotated_uploads/{filename:path}")
def protected_legacy_annotated(filename: str,
                               decoded: dict = Depends(verify_verified_firebase_token)):
    return _legacy_static_image(filename, "annotated", decoded)


# The root frontend mount also maps /uploads and /annotated_uploads to these
# same disk folders. Guard those historical aliases before the catch-all.
@router.get("/uploads/{filename:path}")
def protected_legacy_root_original(filename: str,
                                   decoded: dict = Depends(verify_verified_firebase_token)):
    return _legacy_static_image(filename, "original", decoded)


@router.get("/annotated_uploads/{filename:path}")
def protected_legacy_root_annotated(filename: str,
                                    decoded: dict = Depends(verify_verified_firebase_token)):
    return _legacy_static_image(filename, "annotated", decoded)
