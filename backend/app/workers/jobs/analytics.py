"""The nightly snapshot writer.

Records yesterday's numbers for every active person in every tenant, so a
history query is thirty indexed row reads instead of thirty aggregate scans over
the whole deal table.

**Yesterday, not today.** A snapshot taken at 00:05 for the day that just ended
is complete; one taken for the current day is a partial reading that the next
run would have to correct. The service computes today live instead — the seam
lives in `AnalyticsService.series` and nowhere else.

**Written per owner.** Analytics rolls a scope up by summing the ids the scope
resolver returns, so an org-grain row could only ever answer an admin's
question. See `app/models/analytics.py`.

Idempotent through the upsert: a retry corrects the row rather than duplicating
it or failing on the constraint, which matters because this is a retried job and
the backfill path deliberately re-runs it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from app.core.logging import get_logger
from app.services.analytics import AnalyticsService
from app.workers.context import (
    active_organization_ids,
    system_context,
    tenant_scope,
    unscoped_scope,
)
from app.workers.runner import job

logger = get_logger(__name__)

#: Everything the snapshot reads. Narrow on purpose — the writer records
#: numbers, it does not need to be able to change anything.
_SNAPSHOT_GRANTS: tuple[str, ...] = (
    "leads.view",
    "contacts.view",
    "properties.view",
    "deals.view",
    "tasks.view",
    "activities.view",
)

#: How far back a backfill will go in one run. A tenant that has been down for a
#: week catches up over a week of nightly runs rather than one job holding a
#: worker for an unbounded stretch.
MAX_BACKFILL_DAYS = 30


@job(max_tries=2)
async def snapshot_metrics(ctx: dict[str, Any]) -> int:
    """Write yesterday's readings for every tenant. Returns rows written."""
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
    return await _snapshot_all(yesterday)


@job(organization_arg=0, max_tries=2)
async def backfill_metrics(
    ctx: dict[str, Any], organization_id: str, days: str = "30"
) -> int:
    """Rebuild a tenant's history.

    Exists because the alternative — telling an operator to wait a month for
    charts to fill in after a deployment gap — is not an answer. Bounded at
    `MAX_BACKFILL_DAYS` so one call cannot occupy a worker indefinitely.
    """
    organization = UUID(organization_id)
    span = min(int(days), MAX_BACKFILL_DAYS)
    today = datetime.now(UTC).date()

    written = 0
    for offset in range(1, span + 1):
        written += await _snapshot_one(organization, today - timedelta(days=offset))

    logger.info(
        "metrics_backfilled",
        extra={"organization_id": organization_id, "days": span, "rows": written},
    )
    return written


async def _snapshot_all(snapshot_date: date) -> int:
    async with unscoped_scope() as session:
        organizations = await active_organization_ids(session)

    written = 0
    for organization in organizations:
        written += await _snapshot_one(organization, snapshot_date)

    if written:
        logger.info(
            "metrics_snapshotted",
            extra={"date": snapshot_date.isoformat(), "rows": written},
        )
    return written


async def _snapshot_one(organization_id: UUID, snapshot_date: date) -> int:
    """One tenant, one day. Bound to that tenant, under RLS like any request."""
    async with tenant_scope(organization_id) as session:
        auth = system_context(organization_id, *_SNAPSHOT_GRANTS)
        service = AnalyticsService(session, auth)
        # `now` is pinned to the end of the day being recorded, so a run that
        # starts at 00:05 records the whole of yesterday rather than stopping
        # at the moment the job happened to fire.
        end_of_day = datetime.combine(
            snapshot_date + timedelta(days=1), datetime.min.time(), tzinfo=UTC
        )
        return await service.write_snapshot(snapshot_date, now=end_of_day)
