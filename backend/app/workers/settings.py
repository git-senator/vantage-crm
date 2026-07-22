"""The ARQ worker.

Run with:

    arq app.workers.settings.WorkerSettings

Retry and dead-lettering are **not** configured here. ARQ's job hooks receive
`job_id`, `job_try`, `enqueue_time` and `score` — no function name, no
arguments, no exception — so a hook cannot write a useful failure record
without reading the result back out of Redis. `@job` in `runner.py` does it
instead, on the attempt that decided to give up, with the error in hand.

Cron jobs are staggered on the minute rather than all firing at :00. Two sweeps
starting simultaneously across a dozen tenants is a self-inflicted thundering
herd on a database that is also serving requests.
"""

from __future__ import annotations

from typing import Any, ClassVar, cast

from arq import cron
from arq.typing import WorkerCoroutine

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine
from app.services.storage import get_object_storage
from app.workers.jobs.documents import (
    scan_attachment,
    sweep_abandoned_uploads,
    sweep_scan_backlog,
)
from app.workers.jobs.notifications import notify_task_assigned, send_email
from app.workers.queue import close_queue, redis_settings

logger = get_logger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL, json_output=settings.use_json_logs)
    settings.assert_production_ready()
    # Resolve storage once, at startup, so a misconfigured bucket is a failed
    # deploy rather than a job that dies on its first document.
    reachable = await get_object_storage().verify_configuration()
    logger.info(
        "worker_starting",
        extra={
            "environment": settings.ENVIRONMENT,
            "storage_reachable": reachable,
            "scan_enabled": settings.MALWARE_SCAN_ENABLED,
        },
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    await dispose_engine()
    await close_queue()
    logger.info("worker_stopping")


class WorkerSettings:
    # ClassVar because ARQ reads these off the class; they are configuration,
    # not per-instance state.
    functions: ClassVar[list[Any]] = [
        scan_attachment,
        sweep_abandoned_uploads,
        sweep_scan_backlog,
        notify_task_assigned,
        send_email,
    ]

    cron_jobs: ClassVar[list[Any]] = [
        # Staggered: two sweeps firing together across every tenant is a
        # thundering herd on a database that is also serving requests.
        cron(
            cast(WorkerCoroutine, sweep_abandoned_uploads),
            minute={0, 15, 30, 45},
            second=0,
            run_at_startup=False,
            max_tries=2,
        ),
        cron(
            cast(WorkerCoroutine, sweep_scan_backlog),
            minute={5, 20, 35, 50},
            second=0,
            run_at_startup=False,
            max_tries=2,
        ),
    ]

    redis_settings = redis_settings()
    on_startup = startup
    on_shutdown = shutdown

    max_jobs = get_settings().WORKER_MAX_JOBS
    job_timeout = get_settings().WORKER_JOB_TIMEOUT
    max_tries = get_settings().WORKER_MAX_TRIES
    # Keep results long enough to be inspected after a failure, not so long
    # that Redis becomes a job archive — that is what job_failures is for.
    keep_result = 3600
    # Retry with ARQ's exponential backoff rather than hammering a dependency
    # that is already struggling.
    retry_jobs = True
