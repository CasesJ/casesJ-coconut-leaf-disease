"""
app/infrastructure/firebase/auth.py — Firebase Auth adapter.

Re-exports functions from firebase_config so the rest of the application
imports from a single, stable location.
"""
from firebase_config import (
    create_user,
    delete_user,
    ensure_user_account,
    get_user_by_email,
    verify_token,
)

__all__ = [
    "create_user",
    "delete_user",
    "ensure_user_account",
    "get_user_by_email",
    "verify_token",
]
