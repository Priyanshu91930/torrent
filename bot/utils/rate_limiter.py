"""
rate_limiter.py — Per-user async rate limiting using a sliding window.
Blocks users who exceed RATE_LIMIT_CALLS requests in RATE_LIMIT_PERIOD seconds.
"""

import asyncio
import time
from collections import defaultdict, deque
from typing import Dict, Deque

from bot.utils.logger import log


class RateLimiter:
    """
    Sliding-window rate limiter.

    Usage:
        limiter = RateLimiter(max_calls=5, period=60)
        allowed = await limiter.check(user_id)
    """

    def __init__(self, max_calls: int = 5, period: int = 60):
        self.max_calls = max_calls
        self.period = period
        self._buckets: Dict[int, Deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()
        self._blacklist: set = set()

    async def check(self, user_id: int) -> bool:
        """Return True if request is allowed, False if rate-limited."""
        if user_id in self._blacklist:
            log.warning(f"[RateLimit] Blacklisted user {user_id} attempted access")
            return False

        now = time.monotonic()
        async with self._lock:
            bucket = self._buckets[user_id]
            # Remove timestamps outside the sliding window
            while bucket and now - bucket[0] > self.period:
                bucket.popleft()

            if len(bucket) >= self.max_calls:
                log.warning(
                    f"[RateLimit] User {user_id} exceeded {self.max_calls} "
                    f"calls/{self.period}s"
                )
                return False

            bucket.append(now)
            return True

    async def reset(self, user_id: int) -> None:
        """Clear rate-limit bucket for a user (admin action)."""
        async with self._lock:
            self._buckets[user_id].clear()

    def blacklist(self, user_id: int) -> None:
        self._blacklist.add(user_id)
        log.info(f"[RateLimit] User {user_id} blacklisted")

    def unblacklist(self, user_id: int) -> None:
        self._blacklist.discard(user_id)
        log.info(f"[RateLimit] User {user_id} removed from blacklist")

    def is_blacklisted(self, user_id: int) -> bool:
        return user_id in self._blacklist

    def time_until_reset(self, user_id: int) -> int:
        """Seconds until the oldest entry expires (i.e., next slot opens)."""
        bucket = self._buckets.get(user_id)
        if not bucket:
            return 0
        oldest = bucket[0]
        remaining = self.period - (time.monotonic() - oldest)
        return max(0, int(remaining))

    @property
    def blacklisted_users(self) -> list:
        return list(self._blacklist)


# Singleton
rate_limiter = RateLimiter()
