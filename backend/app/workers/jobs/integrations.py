"""Integration jobs: run a sync, dispatch a CRM event, sweep for due syncs.

The same belt-and-braces shape the rest of the worker uses. A sync run is the
durable record; the connection carries the resume cursor. Retry rides `@job`'s
exponential backoff — the run's `attempts` counter and status make each attempt
visible, and the connection's failure streak (advanced only on a *terminal*
failure) is what auto-disables a permanently broken receiver.

The provider network call happens between two short transactions, never inside
one: a slow external API must not hold a tenant transaction — and a pooled
connection — open for its whole timeout.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.core.config import get_settings
from app.integrations.framework.registry import build_provider_registry
from app.models.automation import WorkflowEvent
from app.models.integration import IntegrationConnection, IntegrationSyncRun
from app.observability import metrics
from app.repositories.integration import (
    IntegrationConnectionRepository,
    IntegrationSubscriptionRepository,
    IntegrationSyncRunRepository,
)
from app.services.integration import IntegrationSyncService
from app.workers.context import (
    active_organization_ids,
    tenant_scope,
    unscoped_scope,
)
from app.workers.queue import JobName, enqueue
from app.workers.runner import job

#: Attempts a sync gets before it is a terminal failure. Kept in step with the
#: `@job` retry limit below so the last attempt records the failure on the run
#: rather than dead-lettering.
SYNC_MAX_ATTEMPTS = 5


@job(organization_arg=1, max_tries=SYNC_MAX_ATTEMPTS)
async def run_integration_sync(
    ctx: dict[str, Any], connection_id: str, organization_id: str, run_id: str
) -> str:
    """Run one sync: refresh the token if stale, call the provider, record it."""
    organization = UUID(organization_id)
    settings = get_settings()
    registry = build_provider_registry(settings)
    run_uuid = UUID(run_id)
    attempt = int(ctx.get("job_try", 1) or 1)

    # 1. Load, begin, and gather what the network call needs, then close the txn.
    async with tenant_scope(organization) as session:
        service = IntegrationSyncService(session, settings, registry)
        loaded = await service.load(run_uuid, organization)
        if loaded is None:
            return "gone"
        run, connection = loaded
        if run.status == "succeeded":
            return "already_done"
        if not connection.is_active:
            await service.record_failure(
                connection, run, error="Connection is not active.", terminal=True
            )
            return "inactive"

        await service.begin_run(run)
        try:
            access_token = await service.access_token_for(connection)
        except Exception as exc:
            terminal = attempt >= SYNC_MAX_ATTEMPTS
            await service.record_failure(
                connection, run, error=f"token: {exc}", terminal=terminal
            )
            metrics.record_integration_sync(
                provider=connection.provider,
                outcome="failed" if terminal else "retry",
                organization_id=organization,
            )
            if not terminal:
                raise
            return "token_failed"
        provider_key = connection.provider
        cursor = connection.config.get("cursor")
        event = {"event_type": run.event_type} if run.trigger == "event" else None

    provider = registry.get(provider_key)

    # 2. The provider call, outside any transaction.
    try:
        outcome = await provider.sync(
            access_token=access_token, cursor=cursor, event=event
        )
    except Exception as exc:
        terminal = attempt >= SYNC_MAX_ATTEMPTS
        async with tenant_scope(organization) as session:
            service = IntegrationSyncService(session, settings, registry)
            loaded = await service.load(run_uuid, organization)
            if loaded is not None:
                run, connection = loaded
                await service.record_failure(
                    connection, run, error=str(exc), terminal=terminal
                )
        metrics.record_integration_sync(
            provider=provider_key,
            outcome="failed" if terminal else "retry",
            organization_id=organization,
        )
        if not terminal:
            raise  # let @job schedule the backed-off retry
        return "failed"

    # 3. Record the success.
    async with tenant_scope(organization) as session:
        service = IntegrationSyncService(session, settings, registry)
        loaded = await service.load(run_uuid, organization)
        if loaded is None:  # pragma: no cover — deleted mid-flight
            return "gone"
        run, connection = loaded
        await service.record_success(connection, run, outcome)
    metrics.record_integration_sync(
        provider=provider_key, outcome="succeeded", organization_id=organization
    )
    return f"synced:{outcome.items_processed}"


@job(organization_arg=1)
async def dispatch_integration_event(
    ctx: dict[str, Any], event_id: str, organization_id: str
) -> str:
    """Fan one outbox event out to the connections subscribed to its type."""
    organization = UUID(organization_id)

    async with tenant_scope(organization) as session:
        event = (
            await session.execute(
                select(WorkflowEvent)
                .where(WorkflowEvent.id == UUID(event_id))
                .where(WorkflowEvent.organization_id == organization)
            )
        ).scalar_one_or_none()
        if event is None:
            return "gone"

        subscriptions = await IntegrationSubscriptionRepository(
            session
        ).active_for_event(organization, event.event_type)
        queued: list[tuple[UUID, UUID]] = []
        for subscription in subscriptions:
            run = IntegrationSyncRun(
                organization_id=organization,
                connection_id=subscription.connection_id,
                trigger="event",
                event_type=event.event_type,
                status="pending",
            )
            session.add(run)
            await session.flush()
            queued.append((subscription.connection_id, run.id))

    for connection_id, run_id in queued:
        await enqueue(
            JobName.RUN_INTEGRATION_SYNC,
            str(connection_id),
            str(organization),
            str(run_id),
            job_id=f"intsync:{run_id}",
        )
    return f"dispatched:{len(queued)}"


@job(max_tries=2)
async def sweep_integration_syncs(ctx: dict[str, Any]) -> int:
    """Queue a periodic sync for each active connection past its interval."""
    settings = get_settings()
    before = datetime.now(UTC) - timedelta(
        minutes=settings.INTEGRATION_SYNC_INTERVAL_MINUTES
    )
    queued = 0

    stale_before = datetime.now(UTC) - timedelta(minutes=2)

    async with unscoped_scope() as session:
        organizations = await active_organization_ids(session)

    for organization in organizations:
        async with tenant_scope(organization) as session:
            connections = IntegrationConnectionRepository(session)
            due = await connections.list_due_for_sync(organization, before=before)
            runs: list[tuple[UUID, UUID]] = []
            for connection in due:
                run = _scheduled_run(connection, organization)
                session.add(run)
                await session.flush()
                runs.append((connection.id, run.id))
            # Re-find any pending run whose fast-path enqueue was lost.
            stale = await IntegrationSyncRunRepository(session).list_stale_pending(
                organization, before=stale_before
            )
            runs.extend((row.connection_id, row.id) for row in stale)

        for connection_id, run_id in runs:
            if await enqueue(
                JobName.RUN_INTEGRATION_SYNC,
                str(connection_id),
                str(organization),
                str(run_id),
                job_id=f"intsync:{run_id}",
            ):
                queued += 1

    return queued


def _scheduled_run(
    connection: IntegrationConnection, organization: UUID
) -> IntegrationSyncRun:
    return IntegrationSyncRun(
        organization_id=organization,
        connection_id=connection.id,
        trigger="scheduled",
        status="pending",
    )
