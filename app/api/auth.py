"""
app/api/auth.py — Authentication endpoints.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.security import verify_firebase_token
from app.domain.expert import is_expert_identity
from app.infrastructure.firebase.auth import verify_token
from app.schemas.auth import LoginRequest, UserResponse
from app.core.config import AUTH_TOKEN_CHECKS_PER_MINUTE
from app.core.rate_limit import client_ip, limiter

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


@router.post("/verify-token", response_model=UserResponse)
async def verify_user_token(request: LoginRequest, http_request: Request):
    """Verify Firebase ID token (called after Firebase client login)."""
    await limiter.rolling(f"auth:{client_ip(http_request)}", AUTH_TOKEN_CHECKS_PER_MINUTE, 60)
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
        # Credential paths and provider diagnostics belong in server logs, never
        # in an authentication response shown to a browser user.
        logger.warning("Firebase ID-token verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not verify your sign-in. Please sign out and sign in again.",
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
