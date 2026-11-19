"""Sliding-window rate limits for abuse-prone endpoints (guest login, password login, assistant,
the admin test message).

With ``REDIS_URL`` set (always in staging and production) the windows live in Redis, so every
API process shares one count. Without it (local development) they live in this process. If
Redis stops answering, the limiter falls back to the in-process window and logs a warning:
limits stay per process for that moment instead of failing every request.
"""

import threading
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Protocol, cast

import redis

from prefix_api.core.config import Settings
from prefix_api.core.logging import get_logger
from prefix_api.domain.errors import RateLimitedError

KEY_PREFIX = "prefix:rate:"

logger = get_logger(__name__)


class RateLimiter(Protocol):
    def check(self, key: str, *, limit: int, window_seconds: float) -> None:
        """Record one hit for ``key``; raise when it already made ``limit`` hits in the window."""

    def refuse_if_full(self, key: str, *, limit: int, window_seconds: float) -> None:
        """Raise when ``key`` already has ``limit`` hits in the window, without recording one."""

    def record(self, key: str, *, window_seconds: float) -> None:
        """Record one hit for ``key`` without checking, for failures found afterwards."""


def _refuse(retry_after_seconds: float) -> RateLimitedError:
    return RateLimitedError(
        f"Too many requests. Try again in {int(retry_after_seconds) + 1} seconds"
    )


class InMemoryRateLimiter:
    """One process's windows. Correct for a single API process (local development)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, *, limit: int, window_seconds: float) -> None:
        with self._lock:
            self._refuse_if_full(key, limit=limit, window_seconds=window_seconds)
            self._hits[key].append(self._clock())

    def refuse_if_full(self, key: str, *, limit: int, window_seconds: float) -> None:
        with self._lock:
            self._refuse_if_full(key, limit=limit, window_seconds=window_seconds)

    def record(self, key: str, *, window_seconds: float) -> None:
        with self._lock:
            self._hits[key].append(self._clock())

    def _refuse_if_full(self, key: str, *, limit: int, window_seconds: float) -> None:
        now = self._clock()
        hits = self._hits[key]
        while hits and hits[0] <= now - window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            raise _refuse(hits[0] + window_seconds - now)


# One atomic step per call: drop hits older than the window, count, and (when asked) add.
# Returns -1 when allowed, otherwise the oldest hit's time so the caller can say when to retry.
# KEYS[1] = window key; ARGV = now (ms), window (ms), limit, add (0/1), member.
_WINDOW_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
if limit >= 0 and redis.call('ZCARD', key) >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  return tonumber(oldest[2])
end
if ARGV[4] == '1' then
  redis.call('ZADD', key, now, ARGV[5])
  redis.call('PEXPIRE', key, window)
end
return -1
"""


class _Script(Protocol):
    def __call__(self, keys: list[str], args: list[str | int]) -> object: ...


class RedisRateLimiter:
    """Windows shared by every API process, as Redis sorted sets of hit times."""

    def __init__(
        self,
        client: redis.Redis,
        *,
        fallback: RateLimiter | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._script = cast(_Script, client.register_script(_WINDOW_SCRIPT))
        self._fallback = fallback or InMemoryRateLimiter()
        self._clock = clock

    def check(self, key: str, *, limit: int, window_seconds: float) -> None:
        if not self._run(key, limit=limit, window_seconds=window_seconds, add=True):
            self._fallback.check(key, limit=limit, window_seconds=window_seconds)

    def refuse_if_full(self, key: str, *, limit: int, window_seconds: float) -> None:
        if not self._run(key, limit=limit, window_seconds=window_seconds, add=False):
            self._fallback.refuse_if_full(key, limit=limit, window_seconds=window_seconds)

    def record(self, key: str, *, window_seconds: float) -> None:
        if not self._run(key, limit=-1, window_seconds=window_seconds, add=True):
            self._fallback.record(key, window_seconds=window_seconds)

    def _run(self, key: str, *, limit: int, window_seconds: float, add: bool) -> bool:
        """True when Redis answered (and the limit held); False to use the fallback."""
        now_ms = int(self._clock() * 1000)
        window_ms = int(window_seconds * 1000)
        try:
            result = self._script(
                keys=[KEY_PREFIX + key],
                args=[
                    now_ms,
                    window_ms,
                    limit,
                    "1" if add else "0",
                    f"{now_ms}:{uuid.uuid4().hex}",
                ],
            )
        except redis.RedisError as exc:
            logger.warning("rate_limit.redis_unavailable", error=type(exc).__name__)
            return False
        oldest = int(cast(int, result))
        if oldest >= 0:
            raise _refuse((oldest + window_ms - now_ms) / 1000)
        return True


def build_rate_limiter(settings: Settings) -> RateLimiter:
    if settings.redis_url is None:
        return InMemoryRateLimiter()
    client = redis.Redis.from_url(
        str(settings.redis_url), socket_connect_timeout=1, socket_timeout=1
    )
    return RedisRateLimiter(client)
