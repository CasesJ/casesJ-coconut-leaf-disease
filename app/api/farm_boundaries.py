"""Authenticated farm-boundary endpoints.  Clients never write counters directly."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.security import verify_verified_firebase_token
from app.domain.expert import is_authorized_expert_identity
from app.services import farm_boundary_service as boundaries

router = APIRouter(prefix="/farm/areas", tags=["farm boundaries"])


class BoundaryPayload(BaseModel):
    farm_name: str = Field(default="", max_length=100)
    center_latitude: float
    center_longitude: float
    boundary_geojson: dict


def _uid(decoded: dict) -> str:
    uid = str(decoded.get("uid") or "").strip()
    if not uid or decoded.get("token_verified_offline"):
        raise HTTPException(401, "Verified Firebase authentication is required")
    if is_authorized_expert_identity(decoded):
        raise HTTPException(403, "Farmer access is required")
    return uid


@router.get("")
def get_areas(decoded: dict = Depends(verify_verified_firebase_token)):
    return {"areas": boundaries.list_boundaries(_uid(decoded))}


@router.put("/{area_id}")
def put_area(area_id: int, payload: BoundaryPayload, decoded: dict = Depends(verify_verified_firebase_token)):
    try:
        uid = _uid(decoded)
        result = boundaries.save_boundary(uid, area_id, payload.farm_name, payload.center_latitude,
                                          payload.center_longitude, payload.boundary_geojson)
        try:
            boundaries.reassociate_existing_detections(uid)
        except Exception:
            # The boundary transaction already succeeded; a later refresh can
            # safely recalculate from immutable original GPS.
            pass
        return result
    except boundaries.BoundaryError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/{area_id}")
def remove_area(area_id: int, decoded: dict = Depends(verify_verified_firebase_token)):
    try:
        uid = _uid(decoded)
        result = boundaries.delete_boundary(uid, area_id)
        try:
            boundaries.reassociate_existing_detections(uid)
        except Exception:
            pass
        return result
    except boundaries.BoundaryError as exc:
        raise HTTPException(422, str(exc)) from exc
