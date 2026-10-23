"""
app/api/records.py — Authenticated record retrieval and expert CSV export.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.domain.detection import apply_record_defaults
from app.services.detection_service import get_user_records
from app.services.media_service import present_detection_record
from app.services.export_service import generate_detections_csv
from app.core.config import EXPORTS_PER_MINUTE
from app.core.rate_limit import limiter
from app.core.security import verify_verified_firebase_token
from app.domain.expert import is_authorized_expert_identity

router = APIRouter(tags=["records"])
logger = logging.getLogger(__name__)


@router.get("/records")
async def get_records(decoded: dict = Depends(verify_verified_firebase_token)):
    """Return only the verified farmer's detection records."""
    if is_authorized_expert_identity(decoded):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Use the authorized expert records workflow",
        )
    user_id = str(decoded.get("uid") or "").strip()
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Verified Firebase sign-in is required")
    try:
        records = get_user_records(user_id)
        for record in records:
            apply_record_defaults(record)
        return [present_detection_record(record) for record in records]
    except Exception as exc:
        logger.exception("Error fetching detection records for authenticated farmer")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Unable to retrieve detection records") from exc


@router.get("/api/export-csv")
async def export_detections_csv(decoded: dict = Depends(verify_verified_firebase_token)):
    """Export the legacy cross-farmer CSV only through expert authorization."""
    if not is_authorized_expert_identity(decoded):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Expert access required")
    user_id = str(decoded.get("uid") or "")
    await limiter.rolling(f"export:{user_id}", EXPORTS_PER_MINUTE, 60)
    try:
        csv_content = generate_detections_csv()
        return StreamingResponse(
            iter([csv_content]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=disease_detections.csv"},
        )
    except Exception as exc:
        logger.error("CSV export error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
