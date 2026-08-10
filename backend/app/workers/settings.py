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
from app.workers.jobs.billing import sweep_subscription_grace
from app.workers.jobs.calendar import sweep_calendar_reminders
from app.workers.jobs.deal_intelligence import (
    rescore_deals,
    rescore_organization_deals,
)
from app.workers.jobs.documents import (
    scan_attachment,
    sweep_abandoned_uploads,
    sweep_scan_backlog,
)
from app.workers.jobs.enterprise import (
    process_data_request,
    sweep_data_retention,
)
from app.workers.jobs.growth_intelligence import (
    recompute_growth,
    recompute_organization_growth,
)
from app.workers.jobs.integrations import (
    dispatch_integration_event,
    run_integration_sync,
    sweep_integration_syncs,
)
from app.workers.jobs.lead_intelligence import (
    rescore_leads,
    rescore_organization_leads,
)
from app.workers.jobs.messaging import deliver_message
from app.workers.jobs.notifications import deliver_notification_email, send_email
from app.workers.jobs.property_intelligence import (
    rescore_organization_properties,
    rescore_properties,
)
from app.workers.jobs.reports import (
    run_report_export,
    sweep_expired_exports,
    sweep_scheduled_reports,
)
from app.workers.jobs.translations import (
    sweep_property_translations,
    translate_property,
)
from app.workers.jobs.webhooks import (
    deliver_webhook,
    dispatch_webhook_event,
    sweep_webhook_deliveries,
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
        rescore_deals,
        rescore_organization_deals,
        rescore_properties,
        rescore_organization_properties,
        translate_property,
        sweep_property_translations,
        recompute_growth,
        recompute_organization_growth,
        dispatch_webhook_event,
        deliver_webhook,
        sweep_webhook_deliveries,
        sweep_subscription_grace,
        run_integration_sync,
        dispatch_integration_event,
        sweep_integration_syncs,
        process_data_request,
        sweep_data_retention,
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
        # Every ten minutes, off the quarter hours. This is the net under a
        # lost enqueue, not the fast path — a listing saved now is translated
        # in seconds — so the cadence only bounds how long a *failure* stays
        # invisible. Ten minutes is short enough that a batch import finishes
        # while someone is still watching it, and long enough that a model
        # outage is not retried into a rate limit.
        cron(
            cast(WorkerCoroutine, sweep_property_translations),
            minute={3, 13, 23, 33, 43, 53},
            second=30,
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
        # Nightly, just after the lead rescore. Deal health is deterministic and
        # reads the analytics stage velocity once per tenant, so a full pass is a
        # rule-engine sweep, not a cost.
        cron(
            cast(WorkerCoroutine, rescore_deals),
            hour={0},
            minute={27},
            second=0,
            run_at_startup=False,
            max_tries=2,
        ),
        # Nightly, just after the deal rescore. Listing quality is deterministic
        # and reads the analytics market stats once per tenant, so a full pass is
        # a rule-engine sweep, not a cost.
        cron(
            cast(WorkerCoroutine, rescore_properties),
            hour={0},
            minute={32},
            second=0,
            run_at_startup=False,
            max_tries=2,
        ),
        # Nightly, after the per-record rescores, so the growth read is computed
        # over freshly-scored data. Deterministic and cheap — it runs the rule
        # engine over the Analytics Engine's aggregates, one pass per tenant.
        cron(
            cast(WorkerCoroutine, recompute_growth),
            hour={0},
            minute={37},
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
        # Every minute, off the automation sweeps' seconds. Delivery retries ride
        # a defer on the fast path; this re-finds any the defer lost, so a due
        # webhook is at most a minute late rather than stuck pending.
        cron(
            cast(WorkerCoroutine, sweep_webhook_deliveries),
            minute=set(range(60)),
            second=50,
            run_at_startup=False,
            max_tries=2,
        ),
        # Hourly. A grace period is measured in days, so checking each hour
        # bounds how long a lapsed subscription keeps its plan to well under one.
        cron(
            cast(WorkerCoroutine, sweep_subscription_grace),
            minute={17},
            second=0,
            run_at_startup=False,
            max_tries=2,
        ),
        # Every ten minutes, off the other sweeps' minutes. A connection past its
        # sync interval is picked up here; the interval itself (default hourly)
        # is the product cadence, this is just how often we check for it.
        cron(
            cast(WorkerCoroutine, sweep_integration_syncs),
            minute={3, 13, 23, 33, 43, 53},
            second=0,
            run_at_startup=False,
            max_tries=2,
        ),
        # Once a day, well clear of the other sweeps. Retention is measured in
        # days, so a daily pass bounds how long a record outlives its window to
        # under one — and it doubles as the net for a lost GDPR-request enqueue.
        cron(
            cast(WorkerCoroutine, sweep_data_retention),
            hour={4},
            minute={20},
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
