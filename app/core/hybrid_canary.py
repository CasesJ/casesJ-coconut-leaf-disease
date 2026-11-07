"""Fail-closed, server-side eligibility checks for a hybrid canary rollout."""
from __future__ import annotations

from app.core import config


def is_hybrid_canary_user(user_id: object) -> bool:
    """Return true only for an explicitly configured Firebase UID.

    This is deliberately server-side.  A browser feature flag is presentation
    only and must never grant access to the direct-upload control plane.
    """
    return (
        config.HYBRID_CANARY_ENABLED
        and config.HYBRID_UPLOAD_ROUTES_ENABLED
        and isinstance(user_id, str)
        and user_id in config.HYBRID_CANARY_USER_UIDS
    )


def is_hybrid_upload_user(user_id: object) -> bool:
    """Allow all verified users only in an explicitly global hybrid deployment."""
    return isinstance(user_id, str) and bool(user_id) and (
        config.INFERENCE_DEPLOYMENT_MODE == "hybrid" or is_hybrid_canary_user(user_id)
    )


def hybrid_routes_enabled() -> bool:
    """Whether dormant hybrid routes may be mounted at all."""
    return (config.INFERENCE_DEPLOYMENT_MODE == "hybrid" or config.HYBRID_CANARY_ENABLED) and (
        config.HYBRID_UPLOAD_ROUTES_ENABLED or config.HYBRID_WORKER_ROUTES_ENABLED
    )
