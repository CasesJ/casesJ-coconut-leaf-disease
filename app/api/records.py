"""
app/api/records.py — Public and authenticated record retrieval and CSV export.
"""
import logging

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.domain.detection import apply_record_defaults
from app.services.detection_service import get_user_records
from app.services.export_service import generate_detections_csv

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
async def export_detections_csv(request: Request):
    """Export all disease detections as CSV with inventory summary."""
    try:
        from app.infrastructure.firebase.auth import verify_token  # noqa: PLC0415
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            try:
                verify_token(auth_header.split(" ", 1)[1])
            except Exception:
                pass

        csv_content = generate_detections_csv()
        return StreamingResponse(
            iter([csv_content]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=disease_detections.csv"},
        )
    except Exception as exc:
        logger.error("CSV export error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
