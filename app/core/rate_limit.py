"""Small in-process rate limiting primitives for costly public endpoints.

For a multi-worker or multi-instance deployment, configure a shared limiter at
the reverse proxy/API gateway too.  This limiter intentionally has no external
dependency so local and single-process deployments are protected by default.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request, status


@dataclass(frozen=True)
class LimitResult:
    remaining: int
    reset_at: datetime


class RateLimiter:
    """Concurrency-safe fixed-window, rolling-window, and quota counters."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._rolling: dict[str, deque[float]] = defaultdict(deque)
        self._image_quota: dict[str, tuple[datetime, int]] = {}
        self._connections: dict[str, int] = defaultdict(int)

    async def rolling(self, key: str, limit: int, window_seconds: int) -> LimitResult:
        now = time.monotonic()
        async with self._lock:
            events = self._rolling[key]
            cutoff = now - window_seconds
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, int(events[0] + window_seconds - now) + 1)
                raise _limited(retry_after, "Too many requests. Please try again later.")
            events.append(now)
            return LimitResult(limit - len(events), datetime.now(timezone.utc) + timedelta(seconds=window_seconds))

    async def image_quota_24h(self, subject: str, limit: int) -> LimitResult:
        """Reserve an upload in a 24-hour window started by that user's first upload.

        The window is anchored and reported in Philippine time (Asia/Manila),
        rather than resetting at a shared calendar boundary.
        """
        now = datetime.now(ZoneInfo("Asia/Manila"))
        async with self._lock:
            started_at, used = self._image_quota.get(subject, (now, 0))
            reset_at = started_at + timedelta(hours=24)
            if now >= reset_at:
                started_at, used = now, 0
                reset_at = started_at + timedelta(hours=24)
            if used >= limit:
                raise _limited(max(1, int((reset_at - now).total_seconds())), "24-hour image inference quota reached.")
            self._image_quota[subject] = (started_at, used + 1)
            return LimitResult(limit - used - 1, reset_at)

    async def acquire_connection(self, key: str, limit: int) -> None:
        async with self._lock:
            if self._connections[key] >= limit:
                raise _limited(60, "Too many active streaming connections.")
            self._connections[key] += 1

    async def release_connection(self, key: str) -> None:
        async with self._lock:
            if self._connections[key] > 1:
                self._connections[key] -= 1
            else:
                self._connections.pop(key, None)


def client_ip(request: Request) -> str:
    """Use proxy forwarding only when the deployment explicitly trusts it."""
    # Import here to avoid a config import cycle in tests.
    from app.core.config import TRUST_PROXY_HEADERS
    if TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return request.client.host if request.client else "unknown"


def _limited(retry_after: int, detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=detail,
        headers={"Retry-After": str(retry_after)},
    )


limiter = RateLimiter()
