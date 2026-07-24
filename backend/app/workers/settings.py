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
from app.workers.jobs.ai import run_completion
from app.workers.jobs.analytics import backfill_metrics, snapshot_metrics
from app.workers.jobs.automation import (
    dispatch_workflow_event,
    execute_workflow_run,
    sweep_workflow_events,
    sweep_workflow_runs,
)
from app.workers.jobs.calendar import sweep_calendar_reminders
from app.workers.jobs.documents import (
    scan_attachment,
    sweep_abandoned_uploads,
    sweep_scan_backlog,
)
from app.workers.jobs.lead_intelligence import (
    rescore_leads,
    rescore_organization_leads,
)
from app.workers.jobs.messaging import deliver_message
from app.workers.jobs.notifications import deliver_notification_email, send_email
from app.workers.jobs.reports import (
    run_report_export,
    sweep_expired_exports,
    sweep_scheduled_reports,
)
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
        sweep_calendar_reminders,
        deliver_notification_email,
        deliver_message,
        send_email,
        dispatch_workflow_event,
        execute_workflow_run,
        sweep_workflow_events,
        sweep_workflow_runs,
        snapshot_metrics,
        backfill_metrics,
        run_report_export,
        sweep_scheduled_reports,
        sweep_expired_exports,
        run_completion,
        rescore_leads,
        rescore_organization_leads,
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
        # Every five minutes. A reminder that arrives four minutes late is
        # still useful; one that arrives an hour late is not, so this is the
        # one sweep whose cadence is a product decision rather than a load one.
        # Every minute. A workflow parked on a "wait 15 minutes" delay that
        # resumes up to a minute late is fine; one that resumes up to fifteen
        # minutes late makes short delays meaningless.
        cron(
            cast(WorkerCoroutine, sweep_workflow_runs),
            minute=set(range(60)),
            second=15,
            run_at_startup=False,
            max_tries=2,
        ),
        # The outbox net. Rarely finds anything — the fast path dispatches in
        # milliseconds — so a minute of latency on the exception is acceptable.
        cron(
            cast(WorkerCoroutine, sweep_workflow_events),
            minute=set(range(60)),
            second=45,
            run_at_startup=False,
            max_tries=2,
        ),
        # Once a day, shortly after midnight UTC, for the day that just ended.
        # Late enough that a deal closed at 23:59 is committed, early enough
        # that the charts are right before anyone opens them. Deliberately not
        # at :00 alongside the quarter-hour sweeps.
        cron(
            cast(WorkerCoroutine, snapshot_metrics),
            hour={0},
            minute={7},
            second=0,
            run_at_startup=False,
            max_tries=2,
        ),
        # Nightly, after the snapshot. Lead scores are deterministic and cheap
        # (no model call), so a full rescore of every tenant's open leads is a
        # rule-engine pass, not a cost — it keeps the prioritised list fresh for
        # a workspace that has not opened a lead to trigger a rescore on read.
        cron(
            cast(WorkerCoroutine, rescore_leads),
            hour={0},
            minute={22},
            second=0,
            run_at_startup=False,
            max_tries=2,
        ),
        # Hourly. A daily report is due once a day, so checking every hour
        # bounds how late one can be at an hour — while a per-minute check
        # would scan every tenant's schedule 1,440 times to find nothing.
        cron(
            cast(WorkerCoroutine, sweep_scheduled_reports),
            minute={12},
            second=0,
            run_at_startup=False,
            max_tries=2,
        ),
        # Once a day, well away from the snapshot. Retention is not urgent —
        # a file that lives an extra hour costs nothing.
        cron(
            cast(WorkerCoroutine, sweep_expired_exports),
            hour={3},
            minute={40},
            second=0,
            run_at_startup=False,
            max_tries=2,
        ),
        cron(
            cast(WorkerCoroutine, sweep_calendar_reminders),
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
            second=30,
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
