from __future__ import annotations

from . import async_utils as asyncio
import hashlib
import time
from collections import defaultdict, deque


class InMemoryRateLimiter:
    """Per-principal sliding-window limiter for a single API process."""

    def __init__(self, requests_per_minute: int):
        self.limit = max(1, requests_per_minute)
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - 60
        async with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.limit:
                retry_after = max(1, int(60 - (now - events[0])))
                return False, retry_after
            events.append(now)
            return True, 0


class RedisRateLimiter:
    """Atomic distributed fixed-window limiter; keys contain only principal hashes."""

    _SCRIPT = """
    local count = redis.call('INCR', KEYS[1])
    if count == 1 then
      redis.call('EXPIRE', KEYS[1], ARGV[1])
    end
    local ttl = redis.call('TTL', KEYS[1])
    return {count, ttl}
    """

    def __init__(self, redis_cache, requests_per_minute: int, *, fail_open: bool = False):
        self.redis_cache = redis_cache
        self.limit = max(1, requests_per_minute)
        self.fail_open = fail_open
        self.fallback = InMemoryRateLimiter(requests_per_minute)

    async def allow(self, key: str) -> tuple[bool, int]:
        try:
            return await asyncio.to_thread(self._allow, key)
        except Exception:
            if not self.fail_open:
                raise
            return await self.fallback.allow(key)

    def _allow(self, key: str) -> tuple[bool, int]:
        digest = hashlib.sha256(key.encode()).hexdigest()
        redis_key = self.redis_cache._key(f"rate:{digest}")
        count, ttl = self.redis_cache.client.eval(self._SCRIPT, 1, redis_key, 60)
        return int(count) <= self.limit, max(1, int(ttl)) if int(count) > self.limit else 0
