"""Pacing primitives: token-bucket rate limiting, jitter and backoff.

Scanning an airline site fast is the thing that gets you blocked, so the
scanner's concurrency is always paired with a rate limit and human-ish jitter.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass


class RateLimiter:
    """Simple async token bucket: at most one release every ``min_interval``.

    A bucket per provider means a slow, heavily-defended site (Qatar) does not
    have to hold back a fast one (Alaska).
    """

    def __init__(self, min_interval: float, jitter: float = 0.35) -> None:
        self.min_interval = max(0.0, min_interval)
        self.jitter = max(0.0, jitter)
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    async def acquire(self) -> None:
        if self.min_interval <= 0 and self.jitter <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            # Jitter the *next* slot so the request stream never looks metronomic.
            spread = self.min_interval * self.jitter
            self._next_at = now + self.min_interval + random.uniform(-spread, spread)


@dataclass
class Backoff:
    """Exponential backoff with full jitter, for retrying a blocked request."""

    base: float = 2.0
    factor: float = 2.0
    max_delay: float = 90.0
    max_attempts: int = 3

    def delay_for(self, attempt: int) -> float:
        """Delay before retry ``attempt`` (1-based)."""
        raw = min(self.base * (self.factor ** max(0, attempt - 1)), self.max_delay)
        return random.uniform(raw * 0.5, raw)

    async def sleep(self, attempt: int) -> float:
        d = self.delay_for(attempt)
        await asyncio.sleep(d)
        return d


async def gather_bounded(coros, limit: int):
    """Run awaitables with at most ``limit`` in flight, preserving order.

    Exceptions come back as values rather than cancelling the whole scan -- one
    provider hitting a wall should never lose the months that did succeed.
    """
    sem = asyncio.Semaphore(max(1, limit))

    async def run(c):
        async with sem:
            return await c

    return await asyncio.gather(*(run(c) for c in coros), return_exceptions=True)
