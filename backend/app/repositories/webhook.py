"""Webhook data access.

Two repositories, the same rules every CRM repository follows: tenant scoping is
a WHERE clause on top of RLS, and keyset pagination for history. The one thing
specific here is `get_or_create_pending`, which makes dispatch idempotent at the
database rather than in the caller — the `(endpoint_id, event_id)` unique
constraint is the source of truth, and a concurrent second dispatch resolves to
the row the first created instead of raising.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.webhook import WebhookDelivery, WebhookEndpoint
from app.repositories.base import BaseRepository
from app.schemas.common import MAX_PAGE_SIZE, Cursor


class WebhookEndpointRepository(BaseRepository[WebhookEndpoint]):
    model = WebhookEndpoint

    async def list_for_org(
        self, organization_id: UUID
    ) -> Sequence[WebhookEndpoint]:
        query = (
            self.scoped_to_organization(select(WebhookEndpoint), organization_id)
            .order_by(WebhookEndpoint.created_at.desc(), WebhookEndpoint.id.desc())
        )
        return list((await self.session.execute(query)).scalars().all())

    async def active_for_event(
        self, organization_id: UUID, event_type: str
    ) -> Sequence[WebhookEndpoint]:
        """Active endpoints in this tenant subscribed to `event_type`.

        JSONB containment (`@>`) so the subscription test is a single indexed
        predicate rather than fetching every endpoint and filtering in Python.
        """
        query = (
            self.scoped_to_organization(select(WebhookEndpoint), organization_id)
            .where(WebhookEndpoint.is_active.is_(True))
            .where(WebhookEndpoint.event_types.contains([event_type]))
        )
        return list((await self.session.execute(query)).scalars().all())


class WebhookDeliveryRepository(BaseRepository[WebhookDelivery]):
    model = WebhookDelivery

    async def _by_event(
        self, endpoint_id: UUID, event_id: UUID, organization_id: UUID
    ) -> WebhookDelivery | None:
        query = (
            select(WebhookDelivery)
            .where(WebhookDelivery.organization_id == organization_id)
            .where(WebhookDelivery.endpoint_id == endpoint_id)
            .where(WebhookDelivery.event_id == event_id)
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def get_or_create_pending(
        self,
        *,
        organization_id: UUID,
        endpoint_id: UUID,
        event_id: UUID,
        event_type: str,
        payload: dict[str, object],
    ) -> tuple[WebhookDelivery, bool]:
        """Return `(delivery, created)`.

        Idempotent on `(endpoint_id, event_id)`: a re-dispatch of the same event
        returns the existing row rather than a second delivery. The insert runs
        in a SAVEPOINT so a concurrent creation raises inside the nested block
        and is resolved by re-reading, without poisoning the caller's
        transaction.
        """
        existing = await self._by_event(endpoint_id, event_id, organization_id)
        if existing is not None:
            return existing, False

        delivery = WebhookDelivery(
            organization_id=organization_id,
            endpoint_id=endpoint_id,
            event_id=event_id,
            event_type=event_type,
            payload=payload,
            status="pending",
        )
        self.session.add(delivery)
        try:
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError:
            raced = await self._by_event(endpoint_id, event_id, organization_id)
            if raced is None:  # pragma: no cover - only if the constraint changed
                raise
            return raced, False
        return delivery, True

    async def list_for_endpoint(
        self,
        endpoint_id: UUID,
        organization_id: UUID,
        *,
        limit: int,
        cursor: Cursor | None = None,
    ) -> tuple[list[WebhookDelivery], bool]:
        """One page of an endpoint's delivery history, newest first."""
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        query = (
            select(WebhookDelivery)
            .where(WebhookDelivery.organization_id == organization_id)
            .where(WebhookDelivery.endpoint_id == endpoint_id)
        )
        if cursor is not None:
            query = query.where(
                func.row(WebhookDelivery.created_at, WebhookDelivery.id)
                < func.row(cursor.created_at, cursor.id)
            )
        query = query.order_by(
            WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc()
        ).limit(limit + 1)
        rows = list((await self.session.execute(query)).scalars().all())
        return rows[:limit], len(rows) > limit

    async def list_retryable(
        self, organization_id: UUID, *, now: datetime, limit: int = 100
    ) -> Sequence[WebhookDelivery]:
        """Pending deliveries whose next attempt is due — the sweep's worklist."""
        query = (
            select(WebhookDelivery)
            .where(WebhookDelivery.organization_id == organization_id)
            .where(WebhookDelivery.status == "pending")
            .where(WebhookDelivery.next_attempt_at.is_not(None))
            .where(WebhookDelivery.next_attempt_at <= now)
            .order_by(WebhookDelivery.next_attempt_at.asc())
            .limit(limit)
        )
        return list((await self.session.execute(query)).scalars().all())
