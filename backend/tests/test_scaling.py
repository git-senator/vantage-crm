"""Production scaling & release readiness (Phase 8.1).

Pure, hermetic unit tests — no database or Redis. They pin the deterministic
guarantees the scaling layer depends on:

  * tenant sharding and queue routing are stable and total functions;
  * the stampede cache recomputes a hot key exactly once under concurrency, and
    fails open;
  * the read endpoint resolves to the replica when set and the primary otherwise;
  * scaling misconfigurations are caught by a pure preflight.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from app.core.cache import StampedeCache, jittered_ttl
from app.core.config import Settings
from app.core.partitioning import (
    DEFAULT_QUEUE,
    QueuePriority,
    all_queue_names,
    is_owned_shard,
    queue_for,
    shard_for,
)

TEST_JWT_SECRET = "test_secret_that_is_at_least_thirty_two_chars"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "JWT_SECRET": SecretStr(TEST_JWT_SECRET),
        "ENVIRONMENT": "test",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------ partitioning


class TestPartitioning:
    def test_shard_is_stable_and_in_range(self) -> None:
        org = UUID("11111111-1111-1111-1111-111111111111")
        first = shard_for(org, 8)
        assert first == shard_for(org, 8)  # deterministic across calls
        assert 0 <= first < 8

    def test_shards_spread_tenants(self) -> None:
        counts = [0] * 4
        for _ in range(400):
            counts[shard_for(uuid4(), 4)] += 1
        # Every shard gets work; no shard is empty or takes everything.
        assert all(c > 0 for c in counts)
        assert max(counts) < 400

    def test_invalid_shard_count(self) -> None:
        with pytest.raises(ValueError):
            shard_for(uuid4(), 0)

    def test_is_owned_shard(self) -> None:
        org = uuid4()
        owner = shard_for(org, 4)
        assert is_owned_shard(org, shard_index=owner, shards=4) is True
        assert is_owned_shard(org, shard_index=(owner + 1) % 4, shards=4) is False

    def test_queue_routing(self) -> None:
        assert queue_for(QueuePriority.DEFAULT) == DEFAULT_QUEUE
        assert queue_for(QueuePriority.HIGH) != DEFAULT_QUEUE
        assert queue_for(QueuePriority.LOW) != queue_for(QueuePriority.HIGH)
        names = all_queue_names()
        assert DEFAULT_QUEUE in names and len(set(names)) == 3


# ------------------------------------------------------------------ cache


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        self.store[key] = value


def _serializing_locker():  # type: ignore[no-untyped-def]
    lock = asyncio.Lock()

    @asynccontextmanager
    async def locker(key: str) -> AsyncIterator[bool]:
        async with lock:
            yield True

    return locker


class TestStampedeCache:
    def test_jittered_ttl_bounds(self) -> None:
        for _ in range(200):
            ttl = jittered_ttl(100, jitter_ratio=0.2)
            assert 100 <= ttl <= 120
        assert jittered_ttl(0) == 0

    async def test_recomputes_once_under_concurrency(self) -> None:
        redis = _FakeRedis()
        cache = StampedeCache(redis=redis, locker=_serializing_locker())
        calls = 0

        async def compute() -> dict[str, int]:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return {"value": 42}

        results = await asyncio.gather(
            *[cache.get_or_set("k", compute) for _ in range(10)]
        )
        assert all(r == {"value": 42} for r in results)
        assert calls == 1  # single-flight: computed once for ten callers

    async def test_hit_skips_compute(self) -> None:
        redis = _FakeRedis()
        cache = StampedeCache(redis=redis, locker=_serializing_locker())

        async def compute() -> str:
            return "fresh"

        await cache.get_or_set("k", compute)
        called = False

        async def compute2() -> str:
            nonlocal called
            called = True
            return "again"

        assert await cache.get_or_set("k", compute2) == "fresh"
        assert called is False

    async def test_invalidate_forces_recompute(self) -> None:
        redis = _FakeRedis()
        cache = StampedeCache(redis=redis, locker=_serializing_locker())
        await cache.get_or_set("k", lambda: _const("first"))
        await cache.invalidate("k")
        assert await cache.get_or_set("k", lambda: _const("second")) == "second"

    async def test_fails_open_when_lock_unavailable(self) -> None:
        redis = _FakeRedis()

        @asynccontextmanager
        async def no_lock(key: str) -> AsyncIterator[bool]:
            yield False  # lock could not be acquired / Redis down

        cache = StampedeCache(redis=redis, locker=no_lock)
        assert await cache.get_or_set("k", lambda: _const("computed")) == "computed"


async def _const(value: str) -> str:
    return value


# ---------------------------------------------------- read replica config


class TestReadReplica:
    def test_no_replica_reads_from_primary(self) -> None:
        settings = _settings()
        assert settings.has_read_replica is False
        assert settings.read_database_url == settings.database_url

    def test_replica_url_uses_replica_host(self) -> None:
        settings = _settings(
            POSTGRES_REPLICA_HOST="replica.internal", POSTGRES_REPLICA_PORT=5433
        )
        assert settings.has_read_replica is True
        assert "replica.internal:5433" in settings.read_database_url
        assert settings.read_database_url != settings.database_url


# --------------------------------------------------- production preflight


class TestScalingWarnings:
    def test_defaults_are_clean(self) -> None:
        assert _settings().scaling_warnings() == []

    def test_replica_pointing_at_primary_warns(self) -> None:
        settings = _settings(
            POSTGRES_HOST="db.internal",
            POSTGRES_PORT=5432,
            POSTGRES_REPLICA_HOST="db.internal",
            POSTGRES_REPLICA_PORT=5432,
        )
        warnings = settings.scaling_warnings()
        assert any("primary" in w for w in warnings)

    def test_small_pool_warns(self) -> None:
        settings = _settings(DB_POOL_SIZE=2, DB_MAX_OVERFLOW=2)
        assert any("headroom" in w for w in settings.scaling_warnings())
