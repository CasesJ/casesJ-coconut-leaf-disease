"""Disabled-by-default HTTP surface for the future hybrid browser upload."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.core.config import HYBRID_UPLOAD_ROUTES_ENABLED
from app.core.hybrid_canary import is_hybrid_upload_user
from app.core.security import verify_verified_firebase_token
from app.domain.expert import is_expert_identity
from app.services.hybrid_uploads import (
    CloudinaryStorageError, HybridJobError, IdempotencyConflict,
    UploadIntentExpired, UploadQuotaExceeded, confirm_upload,
    create_upload_authorization, upload_status,
)

router = APIRouter(prefix="/hybrid/uploads", tags=["hybrid-upload-inactive"])


class UploadIntentRequest(BaseModel):
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{16,128}$")
    source_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    source_bytes: int = Field(ge=1)
    route_type: str = "detection"


class UploadConfirmationRequest(BaseModel):
    asset_id: str = Field(min_length=1, max_length=128)


def _enabled(decoded: dict) -> None:
    if (not HYBRID_UPLOAD_ROUTES_ENABLED or is_expert_identity(decoded)
            or not is_hybrid_upload_user(decoded.get("uid"))):
        # Do not disclose a dormant endpoint or permit accidental rollout.
        raise HTTPException(status_code=404, detail="Not found")


def _uid(decoded: dict) -> str:
    uid = str(decoded.get("uid") or "").strip()
    if not uid:
        raise HTTPException(status_code=401, detail="Verified Firebase sign-in is required")
    return uid


def _as_http_error(error: Exception) -> HTTPException:
    if isinstance(error, UploadQuotaExceeded):
        return HTTPException(429, "Hybrid upload quota exceeded")
    if isinstance(error, (IdempotencyConflict, UploadIntentExpired)):
        return HTTPException(409, str(error))
    if isinstance(error, (CloudinaryStorageError, HybridJobError, ValueError)):
        return HTTPException(400, str(error) or "Hybrid upload request was rejected")
    return HTTPException(503, "Hybrid upload service is temporarily unavailable")


@router.post("/intents")
async def create_intent(payload: UploadIntentRequest,
                        decoded: dict = Depends(verify_verified_firebase_token)):
    _enabled(decoded)
    try:
        return await run_in_threadpool(
            create_upload_authorization,
            user_id=_uid(decoded), idempotency_key=payload.idempotency_key,
            source_sha256=payload.source_sha256.lower(), source_bytes=payload.source_bytes,
            route_type=payload.route_type, user_email=str(decoded.get("email") or ""),
        )
    except Exception as error:
        raise _as_http_error(error) from None


@router.post("/intents/{job_id}/confirm")
async def confirm_intent(job_id: str, payload: UploadConfirmationRequest,
                         decoded: dict = Depends(verify_verified_firebase_token)):
    _enabled(decoded)
    try:
        return await run_in_threadpool(
            confirm_upload, user_id=_uid(decoded), job_id=job_id, asset_id=payload.asset_id,
        )
    except Exception as error:
        raise _as_http_error(error) from None


@router.get("/intents/{job_id}")
async def get_intent(job_id: str, decoded: dict = Depends(verify_verified_firebase_token)):
    _enabled(decoded)
    try:
        return await run_in_threadpool(upload_status, user_id=_uid(decoded), job_id=job_id)
    except Exception as error:
        raise _as_http_error(error) from None
