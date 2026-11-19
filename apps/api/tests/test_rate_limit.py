"""Rate limits: shared through Redis when configured, in this process otherwise."""

import os
import uuid
from typing import cast

import pytest
import redis
from api_testkit import make_test_settings

from prefix_api.core.rate_limit import (
    InMemoryRateLimiter,
    RateLimiter,
    RedisRateLimiter,
    build_rate_limiter,
)
from prefix_api.domain.errors import RateLimitedError


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


class FakeScript:
    """The Lua window script, re-done in Python over a dict of sorted hit times."""

    def __init__(self, store: dict[str, list[tuple[int, str]]], *, broken: bool) -> None:
        self._store = store
        self._broken = broken

    def __call__(self, keys: list[str], args: list[str | int]) -> int:
        if self._broken:
            raise redis.ConnectionError("Redis is down")
        now, window, limit = int(args[0]), int(args[1]), int(args[2])
        hits = [h for h in self._store.get(keys[0], []) if h[0] > now - window]
        self._store[keys[0]] = hits
        if limit >= 0 and len(hits) >= limit:
            return min(h[0] for h in hits)
        if args[3] == "1":
            hits.append((now, str(args[4])))
        return -1


class FakeRedis:
    def __init__(self, *, broken: bool = False) -> None:
        self.store: dict[str, list[tuple[int, str]]] = {}
        self.broken = broken

    def register_script(self, script: str) -> FakeScript:
        assert "ZREMRANGEBYSCORE" in script
        return FakeScript(self.store, broken=self.broken)


def _limiters(clock: Clock) -> list[RateLimiter]:
    return [
        InMemoryRateLimiter(clock=clock),
        RedisRateLimiter(FakeRedis(), clock=clock),  # type: ignore[arg-type]
    ]


@pytest.mark.parametrize("kind", [0, 1], ids=["memory", "redis"])
def test_a_window_fills_and_then_frees(kind: int) -> None:
    clock = Clock()
    limiter = _limiters(clock)[kind]

    for _ in range(3):
        limiter.check("k", limit=3, window_seconds=60)
    with pytest.raises(RateLimitedError, match="Try again in 61 seconds"):
        limiter.check("k", limit=3, window_seconds=60)

    clock.now += 61
    limiter.check("k", limit=3, window_seconds=60)


@pytest.mark.parametrize("kind", [0, 1], ids=["memory", "redis"])
def test_failures_recorded_later_count_against_the_limit(kind: int) -> None:
    clock = Clock()
    limiter = _limiters(clock)[kind]

    limiter.refuse_if_full("login", limit=2, window_seconds=900)
    limiter.record("login", window_seconds=900)
    limiter.record("login", window_seconds=900)

    with pytest.raises(RateLimitedError):
        limiter.refuse_if_full("login", limit=2, window_seconds=900)
    limiter.refuse_if_full("other", limit=2, window_seconds=900)


def test_every_api_process_shares_the_redis_window() -> None:
    clock = Clock()
    shared = FakeRedis()
    first = RedisRateLimiter(shared, clock=clock)  # type: ignore[arg-type]
    second = RedisRateLimiter(shared, clock=clock)  # type: ignore[arg-type]

    first.check("guest:1.2.3.4", limit=2, window_seconds=600)
    second.check("guest:1.2.3.4", limit=2, window_seconds=600)

    with pytest.raises(RateLimitedError):
        first.check("guest:1.2.3.4", limit=2, window_seconds=600)
    assert list(shared.store) == ["prefix:rate:guest:1.2.3.4"]


def test_when_redis_is_down_the_limit_still_holds_in_this_process() -> None:
    clock = Clock()
    limiter = RedisRateLimiter(FakeRedis(broken=True), clock=clock)  # type: ignore[arg-type]

    limiter.check("k", limit=1, window_seconds=60)
    with pytest.raises(RateLimitedError):
        limiter.check("k", limit=1, window_seconds=60)


def test_redis_is_used_only_when_configured() -> None:
    assert isinstance(build_rate_limiter(make_test_settings()), InMemoryRateLimiter)
    configured = make_test_settings(redis_url="redis://localhost:6379/0")
    assert isinstance(build_rate_limiter(configured), RedisRateLimiter)


@pytest.mark.skipif(
    "TEST_REDIS_URL" not in os.environ, reason="needs a Redis (CI sets TEST_REDIS_URL)"
)
def test_the_lua_window_against_a_real_redis() -> None:
    client = redis.Redis.from_url(os.environ["TEST_REDIS_URL"])
    limiter = RedisRateLimiter(client)
    key = f"test:{uuid.uuid4().hex}"
    try:
        limiter.check(key, limit=2, window_seconds=5)
        limiter.record(key, window_seconds=5)
        with pytest.raises(RateLimitedError):
            limiter.check(key, limit=2, window_seconds=5)
        with pytest.raises(RateLimitedError):
            limiter.refuse_if_full(key, limit=2, window_seconds=5)
        limiter.refuse_if_full(key, limit=3, window_seconds=5)
        assert 0 < cast(int, client.pttl(f"prefix:rate:{key}")) <= 5000
    finally:
        client.delete(f"prefix:rate:{key}")
