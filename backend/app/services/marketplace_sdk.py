"""Marketplace SDK & access services (Phase 9.6).

The runtime behind the SDK contracts:

  * ``MarketplaceSdkService`` — the capability registry, compatibility validation,
    and registering an application's SDK requirements.
  * ``MarketplaceAccessService`` — granting and revoking an application's access to
    a tenant's capabilities, and the tenant-scoped access check.
  * ``MarketplaceEventService`` — the foundation for application event
    subscriptions.

Reuse is the rule: capabilities map onto the existing RBAC permissions (an app can
never be granted through the SDK what its granter does not hold), compatibility
reuses the developer SDK's semver layer, the event vocabulary is the webhook
event set, and application ownership is the developer platform's row under RLS.
Every mutation is gated on ``settings.manage`` and audited.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.exceptions import AppError, NotFoundError, PermissionDeniedError
from app.marketplace.sdk import (
    SDK_CAPABILITIES,
    CompatibilityReport,
    required_permissions,
    unsupported_capabilities,
    validate_compatibility,
)
from app.models.developer import MarketplaceApplication
from app.models.marketplace_sdk import (
    MarketplaceApiAccessGrant,
    MarketplaceEventSubscription,
    MarketplaceSdkApplication,
)
from app.models.user import User
from app.repositories.developer import MarketplaceApplicationRepository
from app.repositories.sdk import (
    MarketplaceApiAccessGrantRepository,
    MarketplaceEventSubscriptionRepository,
    MarketplaceSdkApplicationRepository,
)
from app.schemas.marketplace_sdk import (
    AccessGrantRead,
    CompatibilityReportRead,
    EventSubscriptionRead,
    SdkApplicationRead,
    SdkCapabilityRead,
)
from app.services.audit import AuditService
from app.services.rbac import AuthorizationContext
from app.webhooks.events import WEBHOOK_EVENT_TYPES

MANAGE_PERMISSION = "settings.manage"


def _now() -> datetime:
    return datetime.now(UTC)


def _report_read(report: CompatibilityReport) -> CompatibilityReportRead:
    return CompatibilityReportRead(
        sdk_version=report.sdk_version,
        compatible=report.compatible,
        version_ok=report.version_ok,
        reason=report.reason,
        unsupported_capabilities=list(report.unsupported_capabilities),
    )


def _sdk_app_read(row: MarketplaceSdkApplication) -> SdkApplicationRead:
    return SdkApplicationRead(
        id=row.id,
        application_id=row.application_id,
        sdk_version=row.sdk_version,
        capabilities=list(row.capabilities),
        requested_permissions=list(row.requested_permissions),
        status=row.status,
        created_at=row.created_at,
    )


def _grant_read(row: MarketplaceApiAccessGrant) -> AccessGrantRead:
    return AccessGrantRead(
        id=row.id,
        application_id=row.application_id,
        granted_permissions=list(row.granted_permissions),
        active=row.is_active,
        granted_by=row.granted_by,
        revoked_at=row.revoked_at,
        created_at=row.created_at,
    )


def _subscription_read(row: MarketplaceEventSubscription) -> EventSubscriptionRead:
    return EventSubscriptionRead(
        id=row.id,
        application_id=row.application_id,
        event_name=row.event_name,
        enabled=row.enabled,
        created_at=row.created_at,
    )


class _ApplicationScoped:
    """Shared application-ownership loader — the tenant isolation + developer
    ownership check every SDK service performs before touching an application."""

    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.applications = MarketplaceApplicationRepository(session)
        self.audit = AuditService(session)

    async def _load_application(
        self, application_id: UUID
    ) -> MarketplaceApplication:
        application = await self.applications.get(
            application_id, self.auth.organization_id
        )
        if application is None:
            raise NotFoundError("Application not found.")
        return application


# =========================================================== SDK requirements


class MarketplaceSdkService(_ApplicationScoped):
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        super().__init__(session, auth)
        self.sdk_apps = MarketplaceSdkApplicationRepository(session)

    def list_capabilities(self) -> list[SdkCapabilityRead]:
        return [
            SdkCapabilityRead(
                key=cap.key,
                title=cap.title,
                description=cap.description,
                required_permission=cap.required_permission,
                extension_point=cap.extension_point,
            )
            for cap in SDK_CAPABILITIES.values()
        ]

    def validate(
        self, sdk_version: str, capabilities: list[str]
    ) -> CompatibilityReportRead:
        return _report_read(validate_compatibility(sdk_version, capabilities))

    async def register(
        self, actor: User, application_id: UUID, sdk_version: str, capabilities: list[str]
    ) -> SdkApplicationRead:
        self.auth.require(MANAGE_PERMISSION)
        application = await self._load_application(application_id)
        report = validate_compatibility(sdk_version, capabilities)
        if not report.compatible:
            await self.audit.record(
                action=AuditAction.SDK_COMPATIBILITY_FAILED,
                organization_id=self.auth.organization_id,
                actor_id=actor.id,
                actor_email=actor.email,
                entity_type="marketplace_application",
                entity_id=application.id,
                metadata={"sdk_version": sdk_version, "reason": report.reason},
            )
            raise AppError(report.reason)

        permissions = required_permissions(capabilities)
        existing = await self.sdk_apps.get_by_application(
            self.auth.organization_id, application.id
        )
        if existing is None:
            row = MarketplaceSdkApplication(
                organization_id=self.auth.organization_id,
                created_by=actor.id,
                application_id=application.id,
                sdk_version=sdk_version,
                capabilities=sorted(set(capabilities)),
                requested_permissions=permissions,
                status="registered",
            )
            self.session.add(row)
        else:
            existing.sdk_version = sdk_version
            existing.capabilities = sorted(set(capabilities))
            existing.requested_permissions = permissions
            existing.status = "registered"
            row = existing
        await self.session.flush()
        await self.session.refresh(row)
        await self.audit.record(
            action=AuditAction.SDK_APPLICATION_REGISTERED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="marketplace_application",
            entity_id=application.id,
            metadata={"sdk_version": sdk_version, "capabilities": row.capabilities},
        )
        return _sdk_app_read(row)

    async def requirements(self, application_id: UUID) -> SdkApplicationRead:
        self.auth.require(MANAGE_PERMISSION)
        await self._load_application(application_id)
        row = await self.sdk_apps.get_by_application(
            self.auth.organization_id, application_id
        )
        if row is None:
            raise NotFoundError("This application has no SDK requirements registered.")
        return _sdk_app_read(row)

    async def check_permissions(self, application_id: UUID) -> dict[str, list[str]]:
        """Which of an application's requested permissions the current caller
        actually holds, and which they are missing — the RBAC reuse."""
        self.auth.require(MANAGE_PERMISSION)
        row = await self.requirements(application_id)
        held = [p for p in row.requested_permissions if p in self.auth.grants]
        missing = [p for p in row.requested_permissions if p not in self.auth.grants]
        return {"held": held, "missing": missing}


# =========================================================== access grants


class MarketplaceAccessService(_ApplicationScoped):
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        super().__init__(session, auth)
        self.grants = MarketplaceApiAccessGrantRepository(session)

    async def grant(
        self, actor: User, application_id: UUID, capabilities: list[str]
    ) -> AccessGrantRead:
        self.auth.require(MANAGE_PERMISSION)
        application = await self._load_application(application_id)
        unsupported = unsupported_capabilities(capabilities)
        if unsupported:
            raise AppError(f"Unknown SDK capabilities: {', '.join(unsupported)}.")
        # RBAC: a caller can only grant capabilities whose permission they hold —
        # an application can never be granted more than its granter has.
        for permission in required_permissions(capabilities):
            if permission not in self.auth.grants:
                raise PermissionDeniedError(
                    f"You do not hold '{permission}' and cannot grant it."
                )

        granted = sorted(set(capabilities))
        existing = await self.grants.get_active(
            self.auth.organization_id, application.id
        )
        if existing is None:
            grant = MarketplaceApiAccessGrant(
                organization_id=self.auth.organization_id,
                application_id=application.id,
                granted_by=actor.id,
                granted_permissions=granted,
            )
            self.session.add(grant)
        else:
            existing.granted_permissions = granted
            existing.granted_by = actor.id
            grant = existing
        await self.session.flush()
        await self.session.refresh(grant)
        await self._audit(AuditAction.ACCESS_GRANTED, actor, application, granted)
        return _grant_read(grant)

    async def revoke(self, actor: User, application_id: UUID) -> AccessGrantRead:
        self.auth.require(MANAGE_PERMISSION)
        application = await self._load_application(application_id)
        grant = await self.grants.get_active(
            self.auth.organization_id, application.id
        )
        if grant is None:
            raise NotFoundError("No active access grant for this application.")
        grant.revoked_at = _now()
        await self.session.flush()
        await self.session.refresh(grant)
        await self._audit(
            AuditAction.ACCESS_REVOKED, actor, application, list(grant.granted_permissions)
        )
        return _grant_read(grant)

    async def status(self, application_id: UUID) -> AccessGrantRead:
        self.auth.require(MANAGE_PERMISSION)
        application = await self._load_application(application_id)
        grant = await self.grants.get_active(
            self.auth.organization_id, application.id
        )
        if grant is None:
            return AccessGrantRead(
                id=None,
                application_id=application.id,
                granted_permissions=[],
                active=False,
                granted_by=None,
                revoked_at=None,
                created_at=None,
            )
        return _grant_read(grant)

    async def has_access(self, application_id: UUID, capability: str) -> bool:
        """Whether an application currently holds a capability — the tenant-scoped
        access check a runtime consults."""
        grant = await self.grants.get_active(
            self.auth.organization_id, application_id
        )
        return grant is not None and capability in grant.granted_permissions

    async def _audit(
        self,
        action: str,
        actor: User,
        application: MarketplaceApplication,
        capabilities: list[str],
    ) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="marketplace_application",
            entity_id=application.id,
            metadata={"slug": application.slug, "capabilities": capabilities},
        )


# =========================================================== events (foundation)


class MarketplaceEventService(_ApplicationScoped):
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        super().__init__(session, auth)
        self.subscriptions = MarketplaceEventSubscriptionRepository(session)

    def available_events(self) -> list[str]:
        return sorted(WEBHOOK_EVENT_TYPES)

    async def subscribe(
        self, actor: User, application_id: UUID, event_name: str
    ) -> EventSubscriptionRead:
        self.auth.require(MANAGE_PERMISSION)
        application = await self._load_application(application_id)
        if event_name not in WEBHOOK_EVENT_TYPES:
            raise AppError(f"'{event_name}' is not a supported event.")
        if await self.subscriptions.find(
            self.auth.organization_id, application.id, event_name
        ):
            raise AppError("Already subscribed to this event.")
        subscription = MarketplaceEventSubscription(
            organization_id=self.auth.organization_id,
            application_id=application.id,
            event_name=event_name,
        )
        self.session.add(subscription)
        await self.session.flush()
        await self.session.refresh(subscription)
        await self._audit(
            AuditAction.EVENT_SUBSCRIPTION_CREATED, actor, application, event_name
        )
        return _subscription_read(subscription)

    async def unsubscribe(
        self, actor: User, application_id: UUID, event_name: str
    ) -> None:
        self.auth.require(MANAGE_PERMISSION)
        application = await self._load_application(application_id)
        subscription = await self.subscriptions.find(
            self.auth.organization_id, application.id, event_name
        )
        if subscription is None:
            raise NotFoundError("Subscription not found.")
        await self._audit(
            AuditAction.EVENT_SUBSCRIPTION_REMOVED, actor, application, event_name
        )
        await self.session.delete(subscription)
        await self.session.flush()

    async def list_subscriptions(
        self, application_id: UUID
    ) -> list[EventSubscriptionRead]:
        self.auth.require(MANAGE_PERMISSION)
        await self._load_application(application_id)
        rows = await self.subscriptions.list_for_application(
            self.auth.organization_id, application_id
        )
        return [_subscription_read(row) for row in rows]

    async def _audit(
        self,
        action: str,
        actor: User,
        application: MarketplaceApplication,
        event_name: str,
    ) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="marketplace_event_subscription",
            entity_id=application.id,
            metadata={"slug": application.slug, "event": event_name},
        )


__all__ = [
    "MANAGE_PERMISSION",
    "MarketplaceAccessService",
    "MarketplaceEventService",
    "MarketplaceSdkService",
]
