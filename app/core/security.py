"""
app/core/security.py — FastAPI dependency callables for Firebase token verification.

These replace the three helper functions that were defined inline in main.py.
"""
import logging
from typing import Any, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer

from app.infrastructure.firebase.auth import verify_token

logger = logging.getLogger(__name__)

_security = HTTPBearer()


async def verify_firebase_token(credentials: Any = Depends(_security)) -> dict:
    """Mandatory Bearer token verification — raises 401 on failure."""
    token = credentials.credentials
    try:
        return verify_token(token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def verify_firebase_token_optional(
    credentials: Any = Depends(_security),
) -> Optional[dict]:
    """Optional token — returns None instead of raising (offline support)."""
    if not credentials or not credentials.credentials:
        return None
    try:
        return verify_token(credentials.credentials)
    except Exception as exc:
        logger.debug("[OFFLINE] Token verification failed (offline mode): %s", exc)
        return None


async def verify_token_from_request(request: Request) -> Optional[dict]:
    """Extract and verify a Bearer token from a raw Request object."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    try:
        token = auth_header.split(" ", 1)[1]
        return verify_token(token)
    except Exception as exc:
        logger.debug("[OFFLINE] Token verification failed: %s", exc)
        return None
