"""Process-wide token-bucket rate limiter, shared per translation service.

Kept stdlib-only (no heavy deps) since it's imported from `base.py`, which is
on the sidecar boot path.
"""

import threading
import time
from typing import Dict, Optional

# Requests/sec sustained per service, shared by every translator instance and
# BabelDOC worker thread across every concurrent job.
_DEFAULT_QPS = 4.0

# Poll granularity while acquire() is blocked, so a cancelled wait returns
# quickly instead of sleeping out the full computed delay.
_POLL_INTERVAL_S = 0.1


class TokenBucketRateLimiter:
    """Thread-safe token bucket: `capacity` tokens refill at `rate`/sec."""

    def __init__(self, rate: float, capacity: Optional[float] = None):
        self._rate = rate
        self._capacity = capacity if capacity is not None else rate
        self._tokens = self._capacity
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill_locked(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last_refill = now

    def acquire(self, cancel_event: Optional[threading.Event] = None) -> bool:
        """Block until a token is available. Returns False if `cancel_event`
        fires first, True once a token was consumed."""
        while True:
            with self._lock:
                self._refill_locked()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return True
                wait = (1 - self._tokens) / self._rate
            slice_ = min(wait, _POLL_INTERVAL_S)
            if cancel_event is not None:
                if cancel_event.wait(timeout=slice_):
                    return False
            else:
                time.sleep(slice_)


_LIMITERS: Dict[str, TokenBucketRateLimiter] = {}
_LIMITERS_LOCK = threading.Lock()


def get_rate_limiter(service: str, qps: Optional[float] = None) -> TokenBucketRateLimiter:
    """Process-wide singleton per service name, constructed lazily."""
    limiter = _LIMITERS.get(service)
    if limiter is not None:
        return limiter
    with _LIMITERS_LOCK:
        limiter = _LIMITERS.get(service)
        if limiter is not None:
            return limiter
        limiter = TokenBucketRateLimiter(qps if qps is not None else _DEFAULT_QPS)
        _LIMITERS[service] = limiter
        return limiter
