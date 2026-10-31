"""Rate limiter and backoff for the Kalshi live client (OFFLINE core, no I/O).

Why not ``kalshi_public.RateLimiter``: it is a fixed-spacing limiter for public GETs, and importing
``kalshi_public`` pulls in pyarrow/parquet at module load, which the live lane must not need.  The
backoff shape (exponential, capped, 0..jitter extra after the cap) is the same as ``kalshi_public.Client``.

The token bucket is deliberately far below Kalshi's documented Basic-tier write budget (the plan puts
our load orders of magnitude under it); the numbers are defaults the caller sets.
"""

from __future__ import annotations

import random
import time


class TokenBucket:
    """``rate`` tokens/s, up to ``burst`` stored.  ``acquire`` blocks (via ``sleep``) until the cost is paid."""

    def __init__(self, rate: float, burst: float | None = None, clock=time.monotonic, sleep=time.sleep):
        if not rate > 0:
            raise ValueError("rate must be > 0")
        self.rate = float(rate)
        self.burst = float(burst if burst is not None else max(1.0, rate))
        if self.burst < 1.0:
            raise ValueError("burst must be >= 1")
        self.clock, self.sleep = clock, sleep
        self.tokens = self.burst
        self._t = clock()
        self.waited = 0.0  # total seconds slept, for the event log / tests

    def _refill(self):
        now = self.clock()
        self.tokens = min(self.burst, self.tokens + (now - self._t) * self.rate)
        self._t = now

    def acquire(self, cost: float = 1.0) -> float:
        if cost > self.burst:
            raise ValueError("cost exceeds burst; it could never be paid")
        self._refill()
        wait = 0.0
        if self.tokens < cost:
            wait = (cost - self.tokens) / self.rate
            self.sleep(wait)
            self.waited += wait
            self._refill()
        self.tokens -= cost
        return wait


def parse_retry_after(headers) -> float | None:
    """Seconds from a numeric Retry-After header (case-insensitive); None if absent or not numeric."""
    for k, v in (headers or {}).items():
        if str(k).lower() == "retry-after":
            try:
                s = float(str(v).strip())
            except ValueError:
                return None
            return s if s >= 0 else None
    return None


def backoff_delay(attempt: int, base: float = 1.0, cap: float = 60.0, jitter: float = 0.25,
                  rng=random.random, retry_after: float | None = None) -> float:
    """Exponential backoff for 0-based ``attempt``; a server Retry-After is a floor; the cap is applied
    before jitter (as in kalshi_public), and Retry-After is never cut below what the server asked."""
    d = min(cap, base * (2 ** attempt))
    d += d * jitter * rng()
    if retry_after is not None:
        d = max(d, retry_after)
    return d
