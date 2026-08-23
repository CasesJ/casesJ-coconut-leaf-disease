"""
app/api/notifications.py — In-app notification endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.security import verify_firebase_token
from app.domain.notification import get_user_notifications
from app.services.notification_service import clear_notifications, mark_notifications_read

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def get_notifications(decoded: dict = Depends(verify_firebase_token)):
    user_id = str(decoded.get("uid") or "")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user identity")
    notices = get_user_notifications(user_id)
    return {"notifications": notices[:30], "unread_count": sum(1 for n in notices if not n.get("read"))}


@router.post("/clear")
async def clear_user_notifications(decoded: dict = Depends(verify_firebase_token)):
    user_id = str(decoded.get("uid") or "")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user identity")
    clear_notifications(user_id)
    return {"message": "Notifications cleared"}


@router.post("/mark-read")
async def mark_read(decoded: dict = Depends(verify_firebase_token)):
    user_id = str(decoded.get("uid") or "")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user identity")
    mark_notifications_read(user_id)
    return {"message": "Notifications marked as read"}
