"""Enqueueing, from the request side.

Two properties, and the second is the reason this is not three lines of ARQ:

**Enqueueing must never break the request that enqueues.** A queue is by
definition the part of the system doing work the user is not waiting for, so a
Redis blip must not turn a successful task assignment into a 500. `enqueue`
logs and returns `None` on failure. The trade-off is stated rather than
implied: a job lost this way is *lost*, and every job in this system is
therefore either reconstructible from database state (the sweeps re-find their
work on the next tick) or genuinely optional (a notification). Nothing whose
loss would corrupt data goes through here — that work belongs in the caller's
transaction.

**The pool is not the API's Redis client.** ARQ needs its own connection with
its own serialisation; sharing the rate-limiter's client would couple a queue
outage to rate limiting and vice versa.

Job *names* are constants rather than strings at the call site, because a job
enqueued under a name the worker does not register fails silently — it sits in
Redis until it expires, with nothing anywhere saying why.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Final

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class JobName:
    """Every job the worker registers. Enqueue by constant, never by string."""

    SCAN_ATTACHMENT: Final = "scan_attachment"
    SWEEP_ABANDONED_UPLOADS: Final = "sweep_abandoned_uploads"
    SWEEP_SCAN_BACKLOG: Final = "sweep_scan_backlog"
    SEND_EMAIL: Final = "send_email"
    DELIVER_NOTIFICATION_EMAIL: Final = "deliver_notification_email"
    DELIVER_MESSAGE: Final = "deliver_message"
    DISPATCH_WORKFLOW_EVENT: Final = "dispatch_workflow_event"
    EXECUTE_WORKFLOW_RUN: Final = "execute_workflow_run"
    SWEEP_WORKFLOW_EVENTS: Final = "sweep_workflow_events"
    SWEEP_WORKFLOW_RUNS: Final = "sweep_workflow_runs"
    SNAPSHOT_METRICS: Final = "snapshot_metrics"
    BACKFILL_METRICS: Final = "backfill_metrics"
    RUN_REPORT_EXPORT: Final = "run_report_export"
    SWEEP_SCHEDULED_REPORTS: Final = "sweep_scheduled_reports"
    SWEEP_EXPIRED_EXPORTS: Final = "sweep_expired_exports"
    RUN_AI_COMPLETION: Final = "run_completion"
    RESCORE_LEADS: Final = "rescore_leads"
    RESCORE_ORGANIZATION_LEADS: Final = "rescore_organization_leads"
    RESCORE_DEALS: Final = "rescore_deals"
    RESCORE_ORGANIZATION_DEALS: Final = "rescore_organization_deals"


def redis_settings(settings: Settings | None = None) -> RedisSettings:
    resolved = settings or get_settings()
    return RedisSettings(
        host=resolved.REDIS_HOST,
        port=resolved.REDIS_PORT,
        database=resolved.REDIS_DB,
        password=resolved.REDIS_PASSWORD.get_secret_value() or None,
        # Fail fast. The caller has a user waiting and a documented fallback;
        # spending five seconds on retries before taking it is worse than
        # taking it immediately.
        conn_timeout=2,
        conn_retries=1,
    )


_pool: ArqRedis | None = None


async def get_queue() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings())
    return _pool


async def close_queue() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def enqueue(
    job_name: str,
    *args: Any,
    defer: timedelta | None = None,
    job_id: str | None = None,
    **kwargs: Any,
) -> str | None:
    """Queue a job. Returns its id, or `None` if the queue was unreachable.

    `job_id` makes enqueueing idempotent: ARQ refuses a duplicate id while the
    first is still queued or running. Pass one whenever re-enqueueing the same
    work is meaningless — scanning an attachment twice, for instance — and
    leave it out when each call is a distinct piece of work.
    """
    try:
        queue = await get_queue()
        job = await queue.enqueue_job(
            job_name, *args, _defer_by=defer, _job_id=job_id, **kwargs
        )
    except Exception:
        # Deliberately broad: every failure mode here — connection refused,
        # timeout, serialisation — has the same correct response, which is to
        # let the request succeed. The alternative is a queue outage becoming
        # an application outage.
        logger.exception("job_enqueue_failed", extra={"job_name": job_name})
        return None

    if job is None:
        # ARQ returns None when `job_id` is already present. That is the
        # deduplication working, not a failure.
        logger.info(
            "job_already_queued", extra={"job_name": job_name, "job_id": job_id}
        )
        return job_id

    logger.info("job_enqueued", extra={"job_name": job_name, "job_id": job.job_id})
    return str(job.job_id)
