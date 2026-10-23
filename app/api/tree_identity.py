"""Authenticated reference media and expert identity decisions, separate from outcomes."""
import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from app.api.monitoring import _ID, _expert, _farmer
from app.core.rate_limit import limiter
from app.core.security import verify_verified_firebase_token
from app.infrastructure.cloudinary_storage import delete_asset, upload_asset
from app.infrastructure.imaging.tree_reference import read_reference_image, reference_metadata
from app.schemas.monitoring import TreeIdentityReview
from app.services import monitoring_service as store
from app.services import tree_identity_service as identity
from app.services import media_service

router = APIRouter(tags=["tree-identity"])
logger = logging.getLogger(__name__)


def _ids(*values):
    if any(not _ID.fullmatch(value) or len(value) > 128 for value in values):
        raise HTTPException(404, "Monitoring evidence not found")


@router.post("/expert/monitoring/cases/{farmer_id}/{case_id}/tree-reference", status_code=201)
async def register_tree_reference(farmer_id: str, case_id: str, file: UploadFile = File(...),
                                  decoded: dict = Depends(verify_verified_firebase_token)):
    expert_id = _expert(decoded)
    _ids(farmer_id, case_id)
    case = await run_in_threadpool(store.get_case, farmer_id, case_id)
    if not case:
        raise HTTPException(404, "Monitoring case not found")
    if case.get("tree_reference"):
        raise HTTPException(409, "Baseline whole-tree reference is already registered and cannot be replaced")
    contents, content_type = await read_reference_image(file)
    await limiter.rolling(f"tree-reference:{expert_id}", 10, 60)
    metadata = await run_in_threadpool(reference_metadata, case, uuid4().hex, expert_id,
                                      contents, content_type, file.filename or "")
    uploaded = None
    asset = None
    try:
        asset = await run_in_threadpool(upload_asset, contents, content_type, "whole-tree-reference")
        uploaded = {**metadata, "image_asset": asset}
        result = await run_in_threadpool(identity.register_baseline, farmer_id, case_id, uploaded)
        return media_service.present_tree_reference(result)
    except Exception as exc:
        if asset:
            # A transaction may commit while returning an error. Delete only
            # after a successful read proves this exact asset was not stored.
            try:
                persisted = await run_in_threadpool(store.get_case, farmer_id, case_id)
                current = (persisted or {}).get("tree_reference") or {}
                if current.get("image_asset") == asset:
                    return media_service.present_tree_reference(current)
                await run_in_threadpool(delete_asset, asset)
            except Exception:
                logger.warning("Unable to establish whether a new reference was committed")
        if isinstance(exc, identity.IdentityConflict):
            raise HTTPException(409, str(exc)) from exc
        if isinstance(exc, identity.IdentityNotFound):
            raise HTTPException(404, str(exc)) from exc
        logger.exception("Whole-tree reference storage failed")
        raise HTTPException(503, "Reference storage unavailable. Reload the case before retrying.") from exc


@router.put("/expert/monitoring/cases/{farmer_id}/{case_id}/follow-ups/{follow_up_id}/identity-review")
def review_tree_identity(farmer_id: str, case_id: str, follow_up_id: str, payload: TreeIdentityReview,
                         decoded: dict = Depends(verify_verified_firebase_token)):
    expert_id = _expert(decoded)
    _ids(farmer_id, case_id, follow_up_id)
    try:
        return identity.review_identity(farmer_id, case_id, follow_up_id, payload, expert_id)
    except identity.IdentityConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except identity.IdentityNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logger.exception("Identity review storage failed")
        raise HTTPException(503, "Identity review storage unavailable. Reload before retrying.") from exc


def _media(farmer_id: str, case_id: str, reference_id: str, follow_up_id: str | None):
    _ids(farmer_id, case_id, reference_id, *([follow_up_id] if follow_up_id else []))
    case = store.get_case(farmer_id, case_id)
    if not case:
        raise HTTPException(404, "Monitoring case not found")
    record = store.get_follow_up(farmer_id, case_id, follow_up_id) if follow_up_id else case
    reference = (record or {}).get("tree_reference")
    if (not identity.owned_reference(reference, case, follow_up_id)
            or reference["id"] != reference_id):
        raise HTTPException(404, "Whole-tree reference not found")
    try:
        contents, content_type = media_service.read_tree_reference(reference)
    except Exception as exc:
        logger.exception("Could not read private whole-tree evidence")
        raise HTTPException(503, "Whole-tree image is temporarily unavailable") from exc
    return Response(contents, media_type=content_type, headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "Content-Disposition": "inline", "Vary": "Authorization"})


@router.get("/monitoring/cases/{case_id}/tree-references/{reference_id}")
def farmer_reference(case_id: str, reference_id: str, follow_up_id: str | None = None,
                      decoded: dict = Depends(verify_verified_firebase_token)):
    return _media(_farmer(decoded), case_id, reference_id, follow_up_id)


@router.get("/expert/monitoring/cases/{farmer_id}/{case_id}/tree-references/{reference_id}")
def expert_reference(farmer_id: str, case_id: str, reference_id: str, follow_up_id: str | None = None,
                     decoded: dict = Depends(verify_verified_firebase_token)):
    _expert(decoded)
    return _media(farmer_id, case_id, reference_id, follow_up_id)


@router.get("/monitoring/cases/{case_id}/follow-ups/{follow_up_id}/identity-reviews")
def farmer_review_history(case_id: str, follow_up_id: str,
                           decoded: dict = Depends(verify_verified_firebase_token)):
    return _history(_farmer(decoded), case_id, follow_up_id)


@router.get("/expert/monitoring/cases/{farmer_id}/{case_id}/follow-ups/{follow_up_id}/identity-reviews")
def expert_review_history(farmer_id: str, case_id: str, follow_up_id: str,
                           decoded: dict = Depends(verify_verified_firebase_token)):
    _expert(decoded)
    return _history(farmer_id, case_id, follow_up_id)


def _history(farmer_id, case_id, follow_up_id):
    _ids(farmer_id, case_id, follow_up_id)
    try:
        return {"identity_reviews": identity.list_identity_reviews(farmer_id, case_id, follow_up_id)}
    except identity.IdentityNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
