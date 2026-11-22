"""In-process sliding-window rate limiting for auth endpoints.

The app is documented as a single uvicorn worker (SQLite, in-process caches),
so a module-level limiter is sufficient and dependency-free. A multi-worker
deployment would need Redis; the interface is intentionally small so swapping
the store is a one-file change.

Design notes:
- Windows are sliding (deque of monotonic timestamps), not fixed buckets, so a
  burst at a bucket boundary cannot double the allowed attempts.
- `check()` does not consume budget: failed logins are recorded only after the
  credentials are rejected, so a legitimate user logging in/out repeatedly is
  never throttled by their own successes.
- Keys are bounded/pruned so an attacker rotating emails cannot grow memory
  without limit.
"""

from __future__ import annotations

import math
import time
from collections import deque

from fastapi import HTTPException, Request, status

from app.config import get_settings

# Cap the number of tracked keys. Reached only under abuse; pruning drops the
# entries whose window has fully expired and, if still over the cap, the
# oldest-seen keys.
_MAX_KEYS = 10_000


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str, limit: int, window: float) -> float | None:
        """Return seconds until a retry is allowed, or None when under budget."""
        if limit <= 0:
            return None
        bucket = self._hits.get(key)
        if not bucket:
            return None
        cutoff = time.monotonic() - window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if not bucket:
            self._hits.pop(key, None)
            return None
        if len(bucket) < limit:
            return None
        return max(0.0, window - (time.monotonic() - bucket[0]))

    def record(self, key: str, window: float) -> None:
        now = time.monotonic()
        bucket = self._hits.get(key)
        if bucket is None:
            if len(self._hits) >= _MAX_KEYS:
                self.prune(window)
            bucket = deque()
            self._hits[key] = bucket
        bucket.append(now)

    def clear(self, key: str) -> None:
        self._hits.pop(key, None)

    def reset(self) -> None:
        """Testing hook: forget every counter."""
        self._hits.clear()

    def prune(self, window: float) -> None:
        cutoff = time.monotonic() - window
        for key in [k for k, bucket in self._hits.items() if not bucket or bucket[-1] <= cutoff]:
            self._hits.pop(key, None)
        # Still oversized (e.g. many distinct keys inside one window): drop the
        # least recently active entries. Abuse-only path, correctness preserved.
        while len(self._hits) > _MAX_KEYS:
            self._hits.pop(next(iter(self._hits)))


# Module-level store shared by every request in the process.
limiter = SlidingWindowLimiter()


def client_ip(request: Request) -> str:
    """Best-effort client IP, honouring the reverse proxy headers.

    The backend normally sits behind the Next.js rewrite proxy, so
    `request.client.host` is the frontend container for every user. With
    TRUST_PROXY_HEADERS enabled (default) the first X-Forwarded-For hop is the
    browser; disable it when the backend is exposed directly so a client cannot
    spoof its own identity.
    """
    settings = get_settings()
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            first = forwarded.split(",")[0].strip()
            if first:
                return first[:64]
        real_ip = request.headers.get("x-real-ip")
        if real_ip and real_ip.strip():
            return real_ip.strip()[:64]
    client = request.client
    return client.host if client and client.host else "unknown"


def _retry_after_response(retry_after: float) -> HTTPException:
    seconds = max(1, math.ceil(retry_after))
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Too many attempts. Try again in {seconds} seconds.",
        headers={"Retry-After": str(seconds)},
    )


def check_rate_limit(scope: str, key: str, limit: int, window: int) -> None:
    """Raise 429 when `scope:key` has exhausted its budget (does not record)."""
    retry_after = limiter.check(f"{scope}:{key}", limit, window)
    if retry_after is not None:
        raise _retry_after_response(retry_after)


def record_rate_limit(scope: str, key: str, window: int) -> None:
    limiter.record(f"{scope}:{key}", window)


def clear_rate_limit(scope: str, key: str) -> None:
    limiter.clear(f"{scope}:{key}")


def reset_rate_limits() -> None:
    """Testing hook."""
    limiter.reset()
