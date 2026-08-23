"""
app/api/reports.py — PDF report download endpoint.
"""
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.core.security import verify_firebase_token
from app.services.detection_service import get_user_records
from app.services.export_service import build_executive_user_records_pdf

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/my-records.pdf")
async def download_my_records_pdf(decoded: dict = Depends(verify_firebase_token)):
    """Return the authenticated user's detection analytics as a downloadable PDF."""
    user_id = decoded.get("uid")
    email = decoded.get("email", "User")
    records = get_user_records(user_id)
    pdf_bytes = build_executive_user_records_pdf(records, email)
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=coconut-leaf-disease-analytics-report.pdf"},
    )
