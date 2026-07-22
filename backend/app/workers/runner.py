"""The `@job` decorator: retry with backoff, then dead-letter.

Two facts about ARQ drive this design, and both are easy to get wrong by
assuming the framework does more than it does.

**ARQ does not retry ordinary exceptions.** `retry_jobs=True` only re-queues a
job that raises `arq.Retry` (or is cancelled). A job that raises `ValueError` is
marked failed on its first attempt and never runs again. So "failed jobs retry
with backoff" is not a setting — it is this wrapper, translating a natural
exception into `Retry(defer=…)` while attempts remain.

**The job lifecycle hooks receive almost nothing.** `after_job_end` gets
`job_id`, `job_try`, `enqueue_time` and `score` — no function name, no
arguments, no exception. Recording a useful dead-letter row from there means
reading the stored result back out of Redis and hoping it was kept. Doing it
here instead means the row is written by the code that actually has the error in
hand, on the attempt that decided to give up.

Backoff is exponential with jitter. The jitter is not decoration: a burst of
jobs that all fail against the same dependency will otherwise retry in lockstep
forever, which is a synchronised load spike aimed at whatever is already
struggling.
"""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, ParamSpec, TypeVar
from uuid import UUID

from arq import Retry

from app.core.config import get_settings
from app.core.logging import get_logger
from app.workers.dead_letter import clear_failure, job_key, record_failure

logger = get_logger(__name__)

P = ParamSpec("P")
R = TypeVar("R")

#: First retry after ~2s, then 4, 8, 16 … capped. The cap matters more than the
#: base: an unbounded doubling reaches "tomorrow" by attempt fifteen.
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_CAP_SECONDS = 300.0


def backoff_seconds(attempt: int) -> float:
    """Delay before attempt `attempt + 1`, jittered."""
    raw: float = min(
        BACKOFF_BASE_SECONDS * float(2 ** (attempt - 1)), BACKOFF_CAP_SECONDS
    )
    # Full jitter over the lower half: still exponential, no longer synchronised.
    return raw * (0.5 + random.random() * 0.5)  # noqa: S311 — scheduling, not crypto


def _organization_from(args: tuple[Any, ...], index: int | None) -> UUID | None:
    if index is None or index >= len(args):
        return None
    try:
        return UUID(str(args[index]))
    except (ValueError, TypeError):
        return None


def job(
    *,
    organization_arg: int | None = None,
    max_tries: int | None = None,
) -> Callable[
    [Callable[..., Awaitable[R]]], Callable[..., Awaitable[R]]
]:
    """Wrap a job function with retry and dead-letter behaviour.

    `organization_arg` is the positional index (excluding `ctx`) of the tenant
    id, used only to attribute a dead-letter row. It is diagnostic: a wrong or
    missing index costs a filter in the admin view, never correctness.

    The wrapper keeps `__name__`, which is how ARQ registers and how `enqueue`
    addresses the job — so `JobName` constants must match function names.
    """

    def decorator(function: Callable[..., Awaitable[R]]) -> Callable[..., Awaitable[R]]:
        @wraps(function)
        async def wrapper(ctx: dict[str, Any], *args: Any, **kwargs: Any) -> R:
            name = function.__name__
            key = job_key(*args)
            attempt = int(ctx.get("job_try", 1) or 1)
            limit = max_tries or get_settings().WORKER_MAX_TRIES

            try:
                result = await function(ctx, *args, **kwargs)
            except Exception as exc:
                if attempt < limit:
                    delay = backoff_seconds(attempt)
                    logger.warning(
                        "job_retrying",
                        extra={
                            "job_name": name,
                            "job_key": key,
                            "attempt": attempt,
                            "retry_in_seconds": round(delay, 2),
                            "error_class": exc.__class__.__name__,
                        },
                    )
                    # ARQ re-queues on this and nothing else. Chaining `from
                    # exc` keeps the original traceback in the worker log.
                    raise Retry(defer=delay) from exc

                logger.error(
                    "job_dead_lettered",
                    extra={
                        "job_name": name,
                        "job_key": key,
                        "attempts": attempt,
                        "error_class": exc.__class__.__name__,
                    },
                )
                await record_failure(
                    job_name=name,
                    key=key,
                    organization_id=_organization_from(args, organization_arg),
                    error=exc,
                    attempts=attempt,
                    job_args={"args": [str(arg) for arg in args]},
                )
                raise

            # Success closes any row this job/key had open. A no-op when there
            # was none, which is the common case.
            await clear_failure(job_name=name, key=key)
            return result

        return wrapper

    return decorator
