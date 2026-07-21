"""Distributed rate limiting.

The properties that matter:

  * the check-and-increment is atomic, so concurrent requests cannot both
    consume the last slot;
  * a rejected attempt does NOT extend its own window, or a throttled attacker
    could lock out the legitimate account holder indefinitely;
  * layered limits are independent — per-IP and per-account catch different
    attacks and neither can substitute for the other;
  * it fails OPEN, because a limiter that rejects traffic during a cache
    outage turns a degraded dependency into a full one.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.core import rate_limit
from app.core.rate_limit import RateLimit


async def _redis_available() -> bool:
    from app.core.redis import check_redis

    return await check_redis()


@pytest.fixture
def identifier() -> str:
    return f"test-{uuid.uuid4().hex}"


@pytest.fixture
def small_limit() -> RateLimit:
    return RateLimit("test_small", limit=3, window_seconds=60)


class TestSlidingWindow:
    async def test_allows_up_to_the_limit(
        self, identifier: str, small_limit: RateLimit
    ) -> None:
        if not await _redis_available():
            pytest.skip("Redis not reachable")

        for expected_remaining in (2, 1, 0):
            result = await rate_limit.check(small_limit, identifier)
            assert result.allowed
            assert result.remaining == expected_remaining

    async def test_rejects_beyond_the_limit(
        self, identifier: str, small_limit: RateLimit
    ) -> None:
        if not await _redis_available():
            pytest.skip("Redis not reachable")

        for _ in range(small_limit.limit):
            await rate_limit.check(small_limit, identifier)

        result = await rate_limit.check(small_limit, identifier)
        assert not result.allowed
        assert result.reset_after_seconds >= 1, "Retry-After must never be 0"

    async def test_identifiers_are_independent(
        self, small_limit: RateLimit
    ) -> None:
        """One user exhausting their quota must not affect anyone else."""
        if not await _redis_available():
            pytest.skip("Redis not reachable")

        victim = f"victim-{uuid.uuid4().hex}"
        bystander = f"bystander-{uuid.uuid4().hex}"

        for _ in range(small_limit.limit + 2):
            await rate_limit.check(small_limit, victim)

        assert (await rate_limit.check(small_limit, bystander)).allowed

    async def test_limits_are_independent_of_each_other(
        self, identifier: str
    ) -> None:
        """Per-IP and per-account counters must not share a bucket."""
        if not await _redis_available():
            pytest.skip("Redis not reachable")

        first = RateLimit("test_first", limit=2, window_seconds=60)
        second = RateLimit("test_second", limit=2, window_seconds=60)

        for _ in range(3):
            await rate_limit.check(first, identifier)

        assert not (await rate_limit.check(first, identifier)).allowed
        assert (await rate_limit.check(second, identifier)).allowed

    async def test_rejected_attempts_do_not_extend_the_window(
        self, identifier: str
    ) -> None:
        """Critical: a throttled attacker must not be able to prolong a lockout.

        If rejected attempts were recorded, someone hammering an account could
        keep the legitimate owner locked out forever.
        """
        if not await _redis_available():
            pytest.skip("Redis not reachable")

        limit = RateLimit("test_extend", limit=2, window_seconds=2)
        for _ in range(2):
            await rate_limit.check(limit, identifier)

        # Hammer while blocked.
        for _ in range(10):
            assert not (await rate_limit.check(limit, identifier)).allowed

        # The original window still expires on schedule.
        await asyncio.sleep(2.2)
        assert (await rate_limit.check(limit, identifier)).allowed, (
            "Rejected attempts extended the window — a throttled attacker "
            "could lock out the real account holder indefinitely."
        )

    async def test_window_expires(self, identifier: str) -> None:
        if not await _redis_available():
            pytest.skip("Redis not reachable")

        limit = RateLimit("test_expiry", limit=2, window_seconds=1)
        for _ in range(2):
            await rate_limit.check(limit, identifier)
        assert not (await rate_limit.check(limit, identifier)).allowed

        await asyncio.sleep(1.2)
        assert (await rate_limit.check(limit, identifier)).allowed

    async def test_reset_clears_the_counter(
        self, identifier: str, small_limit: RateLimit
    ) -> None:
        """Used after a successful login so earlier typos are forgiven."""
        if not await _redis_available():
            pytest.skip("Redis not reachable")

        for _ in range(small_limit.limit):
            await rate_limit.check(small_limit, identifier)
        assert not (await rate_limit.check(small_limit, identifier)).allowed

        await rate_limit.reset(small_limit, identifier)
        assert (await rate_limit.check(small_limit, identifier)).allowed


class TestAtomicity:
    async def test_concurrent_checks_do_not_oversubscribe(
        self, identifier: str
    ) -> None:
        """The reason check-and-increment is a Lua script.

        A read followed by a separate write lets two requests both observe
        "one slot left" and both take it. Under credential stuffing that race
        is the entire attack.
        """
        if not await _redis_available():
            pytest.skip("Redis not reachable")

        limit = RateLimit("test_atomic", limit=5, window_seconds=60)
        results = await asyncio.gather(
            *(rate_limit.check(limit, identifier) for _ in range(25))
        )

        allowed = sum(1 for r in results if r.allowed)
        assert allowed == limit.limit, (
            f"{allowed} requests were allowed against a limit of {limit.limit}. "
            "The check-and-increment is not atomic."
        )


class TestFailOpen:
    async def test_allows_traffic_when_redis_is_down(
        self, monkeypatch: pytest.MonkeyPatch, identifier: str, small_limit: RateLimit
    ) -> None:
        """A cache outage must not become a site outage.

        Rate limiting is a mitigation, not the primary control — account
        lockout and RBAC still apply when it is unavailable.
        """

        class BrokenRedis:
            async def eval(self, *args: object, **kwargs: object) -> list[int]:
                raise ConnectionError("redis is down")

        monkeypatch.setattr(rate_limit, "get_redis", lambda: BrokenRedis())

        result = await rate_limit.check(small_limit, identifier)
        assert result.allowed
        assert result.remaining == small_limit.limit

    async def test_reset_survives_an_outage(
        self, monkeypatch: pytest.MonkeyPatch, identifier: str, small_limit: RateLimit
    ) -> None:
        class BrokenRedis:
            async def delete(self, *args: object, **kwargs: object) -> int:
                raise ConnectionError("redis is down")

        monkeypatch.setattr(rate_limit, "get_redis", lambda: BrokenRedis())
        await rate_limit.reset(small_limit, identifier)  # must not raise


class TestPolicies:
    def test_account_limit_is_tighter_than_ip(self) -> None:
        """A distributed attack spreads across IPs but converges on one account."""
        assert rate_limit.LOGIN_PER_ACCOUNT.limit < rate_limit.LOGIN_PER_IP.limit

    def test_login_limits_are_aggressive(self) -> None:
        assert rate_limit.LOGIN_PER_ACCOUNT.limit <= 10
        assert rate_limit.LOGIN_PER_ACCOUNT.window_seconds >= 300

    def test_mutations_are_tighter_than_reads(self) -> None:
        assert (
            rate_limit.AUTHENTICATED_MUTATION.limit
            < rate_limit.AUTHENTICATED_GLOBAL.limit
        )

    def test_anonymous_traffic_is_tighter_than_authenticated(self) -> None:
        assert (
            rate_limit.ANONYMOUS_GLOBAL.limit < rate_limit.AUTHENTICATED_GLOBAL.limit
        )
