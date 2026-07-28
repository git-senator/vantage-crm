"""Deterministic work partitioning (Phase 8.1).

Two pure functions that decide *where* work goes, kept out of the queue and the
services so the routing is reviewable and testable in isolation:

  * `queue_for(priority)` maps a priority to an ARQ queue name, so latency-
    sensitive jobs (a webhook delivery, an AI completion a user is waiting on) do
    not sit behind a nightly rescore of every tenant.
  * `shard_for(organization_id, shards)` hashes a tenant onto one of N shards,
    stably. A sweep can then be fanned out per shard across worker replicas so no
    single worker walks every tenant — and because the hash is deterministic, a
    tenant always lands on the same shard regardless of which process asks.

Determinism is the property that matters: the same inputs always resolve the same
way, on any process, across restarts.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from uuid import UUID

#: The base ARQ queue. ARQ's default queue name; jobs with no explicit queue land
#: here, so the default path is unchanged.
DEFAULT_QUEUE = "arq:queue"


class QueuePriority(StrEnum):
    """Latency class for a job. The value is the queue-name suffix."""

    HIGH = "high"
    DEFAULT = "default"
    LOW = "low"


_QUEUE_NAMES: dict[QueuePriority, str] = {
    QueuePriority.HIGH: "arq:queue:high",
    QueuePriority.DEFAULT: DEFAULT_QUEUE,
    QueuePriority.LOW: "arq:queue:low",
}


def queue_for(priority: QueuePriority) -> str:
    """The ARQ queue name for a priority. DEFAULT resolves to the base queue so
    existing enqueues are unaffected."""
    return _QUEUE_NAMES[priority]


def all_queue_names() -> list[str]:
    """Every queue a worker fleet must listen on, so none is silently unserved."""
    return [DEFAULT_QUEUE, _QUEUE_NAMES[QueuePriority.HIGH], _QUEUE_NAMES[QueuePriority.LOW]]


def shard_for(organization_id: UUID, shards: int) -> int:
    """Stably map a tenant onto `[0, shards)`.

    A cryptographic digest rather than Python's `hash()`: `hash()` is salted per
    process (PYTHONHASHSEED), so it would put a tenant on a different shard in
    every worker — exactly the non-determinism a shard assignment must not have.
    """
    if shards < 1:
        raise ValueError("shards must be >= 1")
    digest = hashlib.sha256(str(organization_id).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % shards


def is_owned_shard(organization_id: UUID, *, shard_index: int, shards: int) -> bool:
    """Whether this worker (owning `shard_index` of `shards`) should process a
    tenant. The predicate a sharded sweep filters its tenant list with."""
    return shard_for(organization_id, shards) == shard_index


__all__ = [
    "DEFAULT_QUEUE",
    "QueuePriority",
    "all_queue_names",
    "is_owned_shard",
    "queue_for",
    "shard_for",
]
