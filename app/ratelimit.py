"""A small in-process sliding-window rate limiter.

In-process is a deliberate trade-off: the app runs as a single web service, and
a shared limiter would mean adding Redis — infrastructure, cost, and a failure
mode — to protect endpoints that see a handful of requests a minute. If you
ever scale to multiple instances, each gets its own budget, so the effective
limit multiplies by the instance count. Move to a shared store at that point.
"""

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

_hits: dict[str, deque[float]] = defaultdict(deque)
_lock = threading.Lock()
_last_sweep = 0.0


def client_ip(request: Request) -> str:
    """Render sits behind a proxy, so the socket address is the load balancer.

    Only the first hop of X-Forwarded-For is meaningful, and only because a
    trusted proxy sets it — never trust this header when running without one.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _sweep(now: float, window: float) -> None:
    """Drop buckets nobody has touched recently, so memory can't grow forever."""
    global _last_sweep
    if now - _last_sweep < 300:
        return
    _last_sweep = now
    for key in [k for k, v in _hits.items() if not v or now - v[-1] > window * 2]:
        _hits.pop(key, None)


def check(key: str, limit: int, window_seconds: int, message: str) -> None:
    """Allow `limit` events per `window_seconds` for `key`, else raise 429."""
    now = time.monotonic()
    with _lock:
        _sweep(now, window_seconds)
        bucket = _hits[key]
        cutoff = now - window_seconds
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            retry_after = max(1, int(bucket[0] + window_seconds - now))
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                message,
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)


def clear() -> None:
    """Test helper — resets all buckets."""
    with _lock:
        _hits.clear()
