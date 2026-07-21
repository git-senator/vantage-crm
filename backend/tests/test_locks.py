"""Distributed lock primitive.

Skipped when Redis is unreachable — except the circuit-breaker tests, which
exercise exactly that condition.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.core import locks


async def _redis_available() -> bool:
    from app.core.redis import check_redis

    locks.reset_breaker()
    return await check_redis()


@pytest.fixture
def lock_key() -> str:
    return f"test:lock:{uuid.uuid4().hex}"


class TestAcquireRelease:
    async def test_acquire_returns_a_token(self, lock_key: str) -> None:
        if not await _redis_available():
            pytest.skip("Redis not reachable")
        token = await locks.acquire(lock_key, ttl_ms=2000, wait_ms=200)
        assert token
        await locks.release(lock_key, token)

    async def test_second_holder_is_blocked(self, lock_key: str) -> None:
        if not await _redis_available():
            pytest.skip("Redis not reachable")
        first = await locks.acquire(lock_key, ttl_ms=2000, wait_ms=200)
        second = await locks.acquire(lock_key, ttl_ms=2000, wait_ms=200)

        assert first is not None
        assert second is None, "two holders acquired the same lock"
        await locks.release(lock_key, first)

    async def test_release_frees_the_lock(self, lock_key: str) -> None:
        if not await _redis_available():
            pytest.skip("Redis not reachable")
        first = await locks.acquire(lock_key, ttl_ms=5000, wait_ms=200)
        assert first is not None
        await locks.release(lock_key, first)

        second = await locks.acquire(lock_key, ttl_ms=2000, wait_ms=200)
        assert second is not None
        await locks.release(lock_key, second)

    async def test_release_by_a_non_owner_is_ignored(self, lock_key: str) -> None:
        """Compare-and-delete.

        Without it, a holder whose lock has already expired and been
        re-acquired would delete someone else's lock on its way out.
        """
        if not await _redis_available():
            pytest.skip("Redis not reachable")
        owner = await locks.acquire(lock_key, ttl_ms=5000, wait_ms=200)
        assert owner is not None

        await locks.release(lock_key, "some-other-token")

        # Still held by the original owner.
        assert await locks.acquire(lock_key, ttl_ms=1000, wait_ms=100) is None
        await locks.release(lock_key, owner)

    async def test_lock_expires(self, lock_key: str) -> None:
        """A crashed holder must not block others forever."""
        if not await _redis_available():
            pytest.skip("Redis not reachable")
        first = await locks.acquire(lock_key, ttl_ms=150, wait_ms=100)
        assert first is not None

        await asyncio.sleep(0.3)
        second = await locks.acquire(lock_key, ttl_ms=1000, wait_ms=100)
        assert second is not None, "lock outlived its TTL"
        await locks.release(lock_key, second)

    async def test_guard_yields_true_when_held(self, lock_key: str) -> None:
        if not await _redis_available():
            pytest.skip("Redis not reachable")
        async with locks.guard(lock_key, ttl_ms=2000, wait_ms=200) as held:
            assert held is True

        # Released on exit.
        token = await locks.acquire(lock_key, ttl_ms=1000, wait_ms=200)
        assert token is not None
        await locks.release(lock_key, token)

    async def test_guard_releases_on_exception(self, lock_key: str) -> None:
        if not await _redis_available():
            pytest.skip("Redis not reachable")
        with pytest.raises(RuntimeError):
            async with locks.guard(lock_key, ttl_ms=5000, wait_ms=200):
                raise RuntimeError("boom")

        token = await locks.acquire(lock_key, ttl_ms=1000, wait_ms=200)
        assert token is not None, "guard leaked the lock on an exception"
        await locks.release(lock_key, token)


class TestCircuitBreaker:
    """A Redis outage must cost one timeout, not one per request.

    Otherwise a cache outage becomes a latency outage — and for refresh
    specifically, the added delay can push a legitimate client past the
    rotation grace window and get it flagged as token theft.
    """

    def teardown_method(self) -> None:
        locks.reset_breaker()

    async def test_failure_is_reported_not_raised(
        self, monkeypatch: pytest.MonkeyPatch, lock_key: str
    ) -> None:
        """Fail open: refusing to proceed would log every user out."""

        class BrokenRedis:
            async def set(self, *args: object, **kwargs: object) -> bool:
                raise ConnectionError("redis is down")

        monkeypatch.setattr(locks, "get_redis", lambda: BrokenRedis())
        locks.reset_breaker()

        assert await locks.acquire(lock_key, ttl_ms=1000, wait_ms=200) is None

    async def test_breaker_short_circuits_subsequent_calls(
        self, monkeypatch: pytest.MonkeyPatch, lock_key: str
    ) -> None:
        calls = 0

        class CountingBrokenRedis:
            async def set(self, *args: object, **kwargs: object) -> bool:
                nonlocal calls
                calls += 1
                raise ConnectionError("redis is down")

        monkeypatch.setattr(locks, "get_redis", lambda: CountingBrokenRedis())
        locks.reset_breaker()

        for _ in range(5):
            await locks.acquire(lock_key, ttl_ms=1000, wait_ms=200)

        assert calls == 1, (
            f"Redis was contacted {calls} times while down. The breaker must "
            "short-circuit after the first failure."
        )

    async def test_guard_yields_false_when_redis_is_down(
        self, monkeypatch: pytest.MonkeyPatch, lock_key: str
    ) -> None:
        """Callers must be able to tell they are running unserialised."""

        class BrokenRedis:
            async def set(self, *args: object, **kwargs: object) -> bool:
                raise ConnectionError("redis is down")

        monkeypatch.setattr(locks, "get_redis", lambda: BrokenRedis())
        locks.reset_breaker()

        async with locks.guard(lock_key, ttl_ms=1000, wait_ms=200) as held:
            assert held is False
