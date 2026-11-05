"""
app/api/auth.py — Authentication endpoints.
"""
import hashlib
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request, status
from starlette.concurrency import run_in_threadpool

from app.core.security import verify_verified_firebase_token
from app.domain.expert import is_expert_identity
from app.infrastructure.firebase.auth import get_user_by_email, verify_token
from app.schemas.auth import LoginRequest, RecoveryVerificationRequest, UserProfileRequest, UserResponse
from app.core.config import (
    AUTH_TOKEN_CHECKS_PER_MINUTE,
    PASSWORD_RECOVERY_ATTEMPTS_PER_HOUR,
    PASSWORD_RECOVERY_ATTEMPTS_PER_MINUTE,
)
from app.core.rate_limit import client_ip, limiter

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

_RECOVERY_FAILURE_MESSAGE = "We couldn't verify those recovery details. Please check them and try again."


def _normalize_mobile_number(value: str) -> str:
    """Canonicalise Philippine mobile input without changing stored profile data."""
    digits = re.sub(r"\D", "", value)
    if digits.startswith("63") and len(digits) == 12:
        digits = "0" + digits[2:]
    if digits.startswith("9") and len(digits) == 10:
        digits = "0" + digits
    return digits


def _recovery_details_match(email: str, mobile_number: str) -> bool:
    """Match Firebase Auth's account UID to its Firestore profile phone number."""
    user = get_user_by_email(email)
    if not user:
        return False

    from firebase_admin import firestore

    profile = firestore.client().collection("users").document(user.uid).get()
    profile_data = profile.to_dict() if profile.exists else None
    registered_number = (profile_data or {}).get("contact_number")
    return bool(registered_number) and (
        _normalize_mobile_number(registered_number) == _normalize_mobile_number(mobile_number)
    )


@router.post("/verify-token", response_model=UserResponse)
async def verify_user_token(request: LoginRequest, http_request: Request):
    """Verify Firebase ID token (called after Firebase client login)."""
    await limiter.rolling(f"auth:{client_ip(http_request)}", AUTH_TOKEN_CHECKS_PER_MINUTE, 60)
    try:
        decoded = await run_in_threadpool(verify_token, request.token)
        if decoded.get("token_verified_offline") or not decoded.get("uid"):
            raise ValueError("Firebase ID token could not be verified")
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


@router.post("/recovery/verify")
async def verify_password_recovery(
    recovery: RecoveryVerificationRequest,
    http_request: Request,
):
    """Verify profile details before the browser asks Firebase to send a reset link.

    Firebase's email action code is the expiring, single-use ownership proof and
    is the only route that updates an Auth password.  This endpoint deliberately
    never receives a Firebase Admin credential or updates a Firestore password.
    """
    email = recovery.email.strip().lower()
    email_key = hashlib.sha256(email.encode("utf-8")).hexdigest()
    ip = client_ip(http_request)
    await limiter.rolling(f"recovery:ip:{ip}", PASSWORD_RECOVERY_ATTEMPTS_PER_MINUTE, 60)
    await limiter.rolling(f"recovery:email:{email_key}", PASSWORD_RECOVERY_ATTEMPTS_PER_HOUR, 3600)

    # Apply the same Firebase email/password minimum and never retain the
    # proposed password. The link handler asks for it again after ownership of
    # the mailbox is proven.
    if recovery.new_password != recovery.confirm_new_password or len(recovery.new_password) < 6:
        return {"verified": False, "message": _RECOVERY_FAILURE_MESSAGE}

    try:
        verified = await run_in_threadpool(_recovery_details_match, email, recovery.mobile_number)
    except Exception as exc:
        logger.warning("Password-recovery verification unavailable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Password recovery is temporarily unavailable. Please try again.",
        )

    # The identical failure text is intentional: it does not disclose whether
    # an email exists or whether only the phone number was incorrect.
    return {
        "verified": verified,
        "message": "Verification complete." if verified else _RECOVERY_FAILURE_MESSAGE,
    }


@router.get("/user")
async def get_current_user(decoded: dict = Depends(verify_verified_firebase_token)):
    """Return info for the currently authenticated user."""
    is_expert = is_expert_identity(decoded)
    return {
        "uid": decoded.get("uid"),
        "email": decoded.get("email"),
        "role": "expert" if is_expert else "farmer",
        "is_expert": is_expert,
        "authenticated": True,
    }


@router.post("/profile")
def save_user_profile(
    profile: UserProfileRequest,
    decoded: dict = Depends(verify_verified_firebase_token),
):
    """Save signup profile details to the verified user's Firestore document."""
    try:
        from firebase_admin import firestore

        uid = decoded["uid"]
        firestore.client().collection("users").document(uid).set(
            {
                "uid": uid,
                "email": decoded.get("email"),
                "first_name": profile.first_name.strip(),
                "last_name": profile.last_name.strip(),
                "contact_number": profile.contact_number.strip(),
                "display_name": f"{profile.first_name.strip()} {profile.last_name.strip()}".strip(),
            },
            merge=True,
        )
        return {"message": "Profile saved"}
    except Exception as exc:
        logger.error("Could not save Firebase user profile: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not save your profile details. Please try again.",
        )


@router.post("/logout")
async def logout():
    """Logout endpoint — token invalidation happens on the client side."""
    return {"message": "Logout successful"}
