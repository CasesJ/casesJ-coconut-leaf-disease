"""
app/api/auth.py — Authentication endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.security import verify_firebase_token
from app.domain.expert import is_expert_identity
from app.infrastructure.firebase.auth import verify_token
from app.schemas.auth import LoginRequest, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/verify-token", response_model=UserResponse)
async def verify_user_token(request: LoginRequest):
    """Verify Firebase ID token (called after Firebase client login)."""
    try:
        decoded = verify_token(request.token)
        is_expert = is_expert_identity(decoded)
        return {
            "uid": decoded.get("uid"),
            "email": decoded.get("email"),
            "role": "expert" if is_expert else "farmer",
            "is_expert": is_expert,
            "message": "Token verified successfully",
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
        )


@router.get("/user")
async def get_current_user(decoded: dict = Depends(verify_firebase_token)):
    """Return info for the currently authenticated user."""
    is_expert = is_expert_identity(decoded)
    return {
        "uid": decoded.get("uid"),
        "email": decoded.get("email"),
        "role": "expert" if is_expert else "farmer",
        "is_expert": is_expert,
        "authenticated": True,
    }


@router.post("/logout")
async def logout():
    """Logout endpoint — token invalidation happens on the client side."""
    return {"message": "Logout successful"}
