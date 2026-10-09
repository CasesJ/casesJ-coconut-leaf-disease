"""
app/api/records.py — Public and authenticated record retrieval and CSV export.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.domain.detection import apply_record_defaults
from app.services.detection_service import get_user_records
from app.services.export_service import generate_detections_csv
from app.core.config import EXPORTS_PER_MINUTE
from app.core.rate_limit import limiter
from app.core.security import verify_firebase_token

router = APIRouter(tags=["records"])
logger = logging.getLogger(__name__)


@router.get("/records")
async def get_records(user_id: str = None):
    """Get detection records for a user (public endpoint, user_id as query param)."""
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="user_id query parameter is required")
    try:
        records = get_user_records(user_id)
        for record in records:
            apply_record_defaults(record)
        return records
    except Exception as exc:
        logger.error("Error fetching records: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"Error fetching records: {exc}")


@router.get("/api/export-csv")
async def export_detections_csv(decoded: dict = Depends(verify_firebase_token)):
    """Export all disease detections as CSV with inventory summary."""
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
