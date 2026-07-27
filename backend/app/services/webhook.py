"""Webhook service — management and delivery.

Two halves, split for the same reason the API-key service is:

**Management** (`WebhookService`) runs in a tenant-bound session and is gated on
`settings.manage` — pointing a tenant's events at an external URL is an egress
decision, the same class of act as changing workspace settings. It mints and
seals the HMAC secret (encrypted at rest through the shared `SecretBox`, shown
once), and every mutation is audited.

**Delivery** (`WebhookDeliveryService`) is the machine half the jobs use under a
system context. It fans an outbox event out to matching endpoints as delivery
rows (idempotent on `(endpoint, event)`), hands the job the plaintext secret to
sign with, and records each attempt's outcome — advancing a delivery through
pending → succeeded/exhausted and tracking an endpoint's consecutive failures so
a permanently broken URL is auto-disabled rather than retried forever.
"""

from __future__ import annotations

import secrets as pysecrets
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.events import was_caused_by_automation
from app.core.audit_actions import AuditAction
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.secrets import get_secret_box
from app.models.user import User
from app.models.webhook import WebhookDelivery, WebhookEndpoint
from app.repositories.webhook import (
    WebhookDeliveryRepository,
    WebhookEndpointRepository,
)
from app.schemas.common import Cursor
from app.services.audit import AuditService
from app.services.rbac import AuthorizationContext
from app.webhooks.delivery import DeliveryResult
from app.webhooks.events import build_payload

logger = get_logger(__name__)

MANAGE_PERMISSION = "settings.manage"
ENTITY_TYPE = "webhook_endpoint"

#: Bound into the sealed secret so a signing key lifted into another column
#: fails to open there — the same defence the MFA secret uses.
SECRET_CONTEXT = "webhook_endpoints.secret"  # noqa: S105 — a column name
_SECRET_PREFIX = "whsec_"  # noqa: S105 — a prefix, not a secret


def generate_secret() -> str:
    """A fresh signing secret. Opaque and high-entropy; shown once."""
    return f"{_SECRET_PREFIX}{pysecrets.token_urlsafe(32)}"


class WebhookService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.endpoints = WebhookEndpointRepository(session)
        self.deliveries = WebhookDeliveryRepository(session)
        self.audit = AuditService(session)
        self._box = get_secret_box()

    # -------------------------------------------------------- management

    async def create(
        self,
        actor: User,
        *,
        name: str,
        url: str,
        event_types: list[str],
    ) -> tuple[WebhookEndpoint, str]:
        """Register an endpoint. Returns the row and its secret (shown once)."""
        self.auth.require(MANAGE_PERMISSION)

        secret = generate_secret()
        endpoint = WebhookEndpoint(
            organization_id=self.auth.organization_id,
            created_by=actor.id,
            name=name,
            url=url,
            secret=self._box.encrypt(secret, context=SECRET_CONTEXT),
            event_types=sorted(set(event_types)),
        )
        await self.endpoints.add(endpoint)

        await self._audit(AuditAction.WEBHOOK_CREATED, actor, endpoint)
        logger.info("webhook_created", extra={"endpoint_id": str(endpoint.id)})
        return endpoint, secret

    async def list_endpoints(self) -> list[WebhookEndpoint]:
        self.auth.require(MANAGE_PERMISSION)
        return list(await self.endpoints.list_for_org(self.auth.organization_id))

    async def get_endpoint(self, endpoint_id: UUID) -> WebhookEndpoint:
        self.auth.require(MANAGE_PERMISSION)
        return await self._load(endpoint_id)

    async def update(
        self,
        actor: User,
        endpoint_id: UUID,
        *,
        name: str | None = None,
        url: str | None = None,
        event_types: list[str] | None = None,
        is_active: bool | None = None,
    ) -> WebhookEndpoint:
        """Partial update. Re-enabling clears the auto-disable marker."""
        self.auth.require(MANAGE_PERMISSION)
        endpoint = await self._load(endpoint_id)

        if name is not None:
            endpoint.name = name
        if url is not None:
            endpoint.url = url
        if event_types is not None:
            endpoint.event_types = sorted(set(event_types))
        if is_active is not None:
            endpoint.is_active = is_active
            if is_active:
                # A manual re-enable is a fresh start: clear the disable marker
                # and the failure streak so one past outage does not re-trip it.
                endpoint.disabled_at = None
                endpoint.consecutive_failures = 0
        await self.session.flush()
        await self._reload(endpoint)

        await self._audit(AuditAction.WEBHOOK_UPDATED, actor, endpoint)
        logger.info("webhook_updated", extra={"endpoint_id": str(endpoint.id)})
        return endpoint

    async def rotate_secret(
        self, actor: User, endpoint_id: UUID
    ) -> tuple[WebhookEndpoint, str]:
        """Issue a fresh signing secret, invalidating the previous one."""
        self.auth.require(MANAGE_PERMISSION)
        endpoint = await self._load(endpoint_id)

        secret = generate_secret()
        endpoint.secret = self._box.encrypt(secret, context=SECRET_CONTEXT)
        await self.session.flush()
        await self._reload(endpoint)

        await self._audit(AuditAction.WEBHOOK_SECRET_ROTATED, actor, endpoint)
        logger.info("webhook_secret_rotated", extra={"endpoint_id": str(endpoint.id)})
        return endpoint, secret

    async def delete(self, actor: User, endpoint_id: UUID) -> None:
        """Remove an endpoint. Its delivery history goes with it (CASCADE)."""
        self.auth.require(MANAGE_PERMISSION)
        endpoint = await self._load(endpoint_id)

        await self._audit(AuditAction.WEBHOOK_DELETED, actor, endpoint)
        await self.session.delete(endpoint)
        await self.session.flush()
        logger.info("webhook_deleted", extra={"endpoint_id": str(endpoint_id)})

    async def list_deliveries(
        self, endpoint_id: UUID, *, limit: int, cursor: Cursor | None
    ) -> tuple[list[WebhookDelivery], bool]:
        self.auth.require(MANAGE_PERMISSION)
        await self._load(endpoint_id)  # 404 if not this tenant's endpoint
        return await self.deliveries.list_for_endpoint(
            endpoint_id, self.auth.organization_id, limit=limit, cursor=cursor
        )

    # ---------------------------------------------------------- helpers

    async def _load(self, endpoint_id: UUID) -> WebhookEndpoint:
        from app.core.exceptions import NotFoundError

        endpoint = await self.endpoints.get(endpoint_id, self.auth.organization_id)
        if endpoint is None:
            raise NotFoundError("Webhook endpoint not found.")
        return endpoint

    async def _reload(self, endpoint: WebhookEndpoint) -> None:
        """Re-read `updated_at` after a write.

        Its server-side `onupdate` marks it expired on flush; without this the
        next attribute access — the response projection — would lazy-load from
        outside the async context and raise MissingGreenlet.
        """
        await self.session.refresh(endpoint, ["updated_at"])

    async def _audit(
        self, action: str, actor: User, endpoint: WebhookEndpoint
    ) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=endpoint.id,
            metadata={
                "name": endpoint.name,
                "url": endpoint.url,
                "events": list(endpoint.event_types),
                "is_active": endpoint.is_active,
            },
        )


class WebhookDeliveryService:
    """The delivery half, used by the dispatch and send jobs.

    No `settings.manage` gate: it runs under a system context bound to one
    tenant, and takes no user-facing action — it fans events out and records
    outcomes. It never mints an endpoint or widens anything.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.endpoints = WebhookEndpointRepository(session)
        self.deliveries = WebhookDeliveryRepository(session)
        self._box = get_secret_box()

    async def dispatch(self, event: object, organization_id: UUID) -> list[UUID]:
        """Fan one outbox event out to matching endpoints.

        Returns the ids of deliveries that need sending — freshly created ones,
        and any pre-existing ones still pending (a re-dispatch that lost its send
        job). A workflow's own change never fans out: it carries the automation
        marker, and mirroring the workflow dispatcher's refusal keeps a webhook
        from being an amplifier for an automation loop.
        """
        event_type: str = event.event_type  # type: ignore[attr-defined]
        event_id: UUID = event.id  # type: ignore[attr-defined]
        payload: dict[str, object] = dict(event.payload or {})  # type: ignore[attr-defined]

        if was_caused_by_automation(payload):
            return []

        endpoints = await self.endpoints.active_for_event(
            organization_id, event_type
        )
        to_send: list[UUID] = []
        for endpoint in endpoints:
            delivery, created = await self.deliveries.get_or_create_pending(
                organization_id=organization_id,
                endpoint_id=endpoint.id,
                event_id=event_id,
                event_type=event_type,
                payload={},
            )
            if created:
                delivery.payload = build_payload(
                    delivery_id=delivery.id,
                    event_id=event_id,
                    event_type=event_type,
                    organization_id=organization_id,
                    event_payload=payload,
                )
                await self.session.flush()
                to_send.append(delivery.id)
            elif delivery.status == "pending":
                to_send.append(delivery.id)
        return to_send

    async def load(
        self, delivery_id: UUID, organization_id: UUID
    ) -> tuple[WebhookDelivery, WebhookEndpoint] | None:
        delivery = await self.deliveries.get(delivery_id, organization_id)
        if delivery is None:
            return None
        endpoint = await self.endpoints.get(delivery.endpoint_id, organization_id)
        if endpoint is None:  # pragma: no cover - CASCADE keeps these together
            return None
        return delivery, endpoint

    def plaintext_secret(self, endpoint: WebhookEndpoint) -> str:
        """The endpoint's signing secret, opened for one delivery."""
        return self._box.decrypt(endpoint.secret, context=SECRET_CONTEXT)

    async def mark_succeeded(
        self,
        delivery: WebhookDelivery,
        endpoint: WebhookEndpoint,
        *,
        attempt: int,
        result: DeliveryResult,
    ) -> None:
        now = datetime.now(UTC)
        delivery.status = "succeeded"
        delivery.attempts = attempt
        delivery.response_status = result.status_code
        delivery.response_body = result.body_snippet
        delivery.error = None
        delivery.next_attempt_at = None
        delivery.delivered_at = now

        endpoint.consecutive_failures = 0
        endpoint.last_success_at = now
        await self.session.flush()

    async def mark_failed(
        self,
        delivery: WebhookDelivery,
        endpoint: WebhookEndpoint,
        *,
        attempt: int,
        result: DeliveryResult,
        retry: bool,
        next_attempt_at: datetime | None,
    ) -> None:
        """Record a failed attempt. `retry` decides pending vs exhausted.

        An endpoint's failure streak is incremented only when a delivery is
        finally exhausted, not per attempt — so the auto-disable threshold
        counts dead deliveries, not retries of one.
        """
        now = datetime.now(UTC)
        delivery.attempts = attempt
        delivery.response_status = result.status_code
        delivery.response_body = result.body_snippet
        delivery.error = result.error

        if retry:
            delivery.status = "pending"
            delivery.next_attempt_at = next_attempt_at
            await self.session.flush()
            return

        delivery.status = "exhausted"
        delivery.next_attempt_at = None

        endpoint.consecutive_failures += 1
        endpoint.last_failure_at = now
        if endpoint.consecutive_failures >= self.settings.WEBHOOK_DISABLE_AFTER_FAILURES:
            endpoint.is_active = False
            endpoint.disabled_at = now
            logger.warning(
                "webhook_endpoint_auto_disabled",
                extra={
                    "endpoint_id": str(endpoint.id),
                    "consecutive_failures": endpoint.consecutive_failures,
                },
            )
        await self.session.flush()


__all__ = [
    "MANAGE_PERMISSION",
    "WebhookDeliveryService",
    "WebhookService",
    "generate_secret",
]
