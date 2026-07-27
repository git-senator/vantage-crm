"""Webhook jobs: dispatch an event, deliver it, sweep for the missed.

The same belt-and-braces shape the rest of the worker uses. The outbox event is
the durable record on the dispatch side; the delivery row is the durable record
on the send side, so a lost job is latency, never a dropped event.

Retry is owned here rather than delegated to `@job`'s exception-retry, because a
delivery's attempt count must survive across worker restarts and be visible in
the delivery history — so it lives on the row. A failed attempt records itself,
schedules the next with the shared exponential backoff, and re-enqueues with a
defer; the sweep re-enqueues anything that defer lost. `@job` still wraps every
job, so an unexpected bug (not a consumer returning 500) still dead-letters.

The HTTP POST happens between two short transactions, never inside one: a slow
consumer must not hold a tenant transaction — and a connection — open for the
whole timeout.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.automation import WorkflowEvent
from app.repositories.webhook import WebhookDeliveryRepository
from app.services.webhook import WebhookDeliveryService
from app.webhooks.delivery import post, serialize
from app.webhooks.signing import build_headers
from app.workers.context import (
    active_organization_ids,
    tenant_scope,
    unscoped_scope,
)
from app.workers.queue import JobName, enqueue
from app.workers.runner import backoff_seconds, job

logger = get_logger(__name__)


@job(organization_arg=1)
async def dispatch_webhook_event(
    ctx: dict[str, Any], event_id: str, organization_id: str
) -> str:
    """Fan one outbox event out to matching webhook endpoints.

    Idempotent through the `(endpoint, event)` unique constraint: a duplicate
    delivery or the sweep re-running this resolves to the existing rows rather
    than fanning out a second copy.
    """
    organization = UUID(organization_id)
    settings = get_settings()

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

        delivery_ids = await WebhookDeliveryService(session, settings).dispatch(
            event, organization
        )

    for delivery_id in delivery_ids:
        await enqueue(
            JobName.DELIVER_WEBHOOK,
            str(delivery_id),
            str(organization),
            job_id=f"whsend:{delivery_id}",
        )
    return f"dispatched:{len(delivery_ids)}"


@job(organization_arg=1)
async def deliver_webhook(
    ctx: dict[str, Any], delivery_id: str, organization_id: str
) -> str:
    """Send one delivery, recording the outcome and scheduling any retry.

    The attempt number comes from the row, not from `ctx` — each retry is a
    freshly enqueued job, so the count must be durable to survive restarts and
    to appear in the history.
    """
    organization = UUID(organization_id)
    settings = get_settings()
    delivery_uuid = UUID(delivery_id)

    # 1. Read what we need and settle terminal cases, then close the txn.
    async with tenant_scope(organization) as session:
        service = WebhookDeliveryService(session, settings)
        loaded = await service.load(delivery_uuid, organization)
        if loaded is None:
            return "gone"
        delivery, endpoint = loaded
        if delivery.status in ("succeeded", "exhausted"):
            return delivery.status
        if not endpoint.is_active:
            # Disabled between dispatch and send: abandon rather than deliver to
            # an endpoint its owner has switched off.
            await service.mark_failed(
                delivery,
                endpoint,
                attempt=delivery.attempts + 1,
                result=_inactive_result(),
                retry=False,
                next_attempt_at=None,
            )
            return "endpoint_inactive"

        attempt = delivery.attempts + 1
        body = serialize(delivery.payload)
        headers = build_headers(
            secret=service.plaintext_secret(endpoint),
            timestamp=str(int(time.time())),
            body=body,
            event_type=delivery.event_type,
            delivery_id=str(delivery.id),
        )
        url = endpoint.url

    # 2. The network call, outside any transaction.
    result = await post(
        url,
        body=body,
        headers=headers,
        timeout_seconds=settings.WEBHOOK_TIMEOUT_SECONDS,
        snippet_bytes=settings.WEBHOOK_RESPONSE_SNIPPET_BYTES,
    )

    # 3. Record the outcome, and schedule a retry if attempts remain.
    retry = not result.ok and attempt < settings.WEBHOOK_MAX_ATTEMPTS
    delay = backoff_seconds(attempt) if retry else 0.0
    next_attempt_at = (
        datetime.now(UTC) + timedelta(seconds=delay) if retry else None
    )

    async with tenant_scope(organization) as session:
        service = WebhookDeliveryService(session, settings)
        loaded = await service.load(delivery_uuid, organization)
        if loaded is None:  # pragma: no cover - deleted mid-flight
            return "gone"
        delivery, endpoint = loaded
        if result.ok:
            await service.mark_succeeded(
                delivery, endpoint, attempt=attempt, result=result
            )
            return "succeeded"
        await service.mark_failed(
            delivery,
            endpoint,
            attempt=attempt,
            result=result,
            retry=retry,
            next_attempt_at=next_attempt_at,
        )

    if retry:
        await enqueue(
            JobName.DELIVER_WEBHOOK,
            delivery_id,
            organization_id,
            defer=timedelta(seconds=delay),
        )
        return f"retry:{attempt}"

    logger.warning(
        "webhook_delivery_exhausted",
        extra={"delivery_id": delivery_id, "attempts": attempt},
    )
    return "exhausted"


@job(max_tries=2)
async def sweep_webhook_deliveries(ctx: dict[str, Any]) -> int:
    """Re-enqueue due deliveries the fast path lost. Returns how many.

    The net that lets the delivery re-enqueue be best-effort: the row carries
    the schedule, so a lost defer is a delay, not a dropped delivery.
    """
    resumed = 0
    now = datetime.now(UTC)

    async with unscoped_scope() as session:
        organizations = await active_organization_ids(session)

    for organization in organizations:
        async with tenant_scope(organization) as session:
            due = await WebhookDeliveryRepository(session).list_retryable(
                organization, now=now
            )
            delivery_ids = [row.id for row in due]

        for delivery_id in delivery_ids:
            if await enqueue(
                JobName.DELIVER_WEBHOOK, str(delivery_id), str(organization)
            ):
                resumed += 1

    if resumed:
        logger.info("webhook_deliveries_swept", extra={"count": resumed})
    return resumed


def _inactive_result() -> Any:
    from app.webhooks.delivery import DeliveryResult

    return DeliveryResult(
        ok=False,
        status_code=None,
        body_snippet=None,
        error="Endpoint was disabled before delivery.",
    )
