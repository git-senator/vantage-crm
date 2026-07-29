"""Marketplace SDK & access layer (Phase 9.6).

The properties that carry the milestone:

  * **Compatibility is deterministic** — a version outside the supported range or
    an unknown capability is refused, reusing the developer SDK's semver rule.
  * **Capabilities map to RBAC** — an application's requested permissions are the
    capabilities' mapped permissions, and a caller can only grant what they hold.
  * **Access grants and revokes** are tenant-scoped and audited; the event layer
    validates against the supported event set.
  * **Tenant isolation holds** (an application is invisible to another workspace),
    RBAC gates every mutation, and every act is audited.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, NotFoundError, PermissionDeniedError
from app.core.permissions import Scope
from app.marketplace.sdk import (
    is_known_capability,
    required_permissions,
    sdk_capability,
    unsupported_capabilities,
    validate_compatibility,
)
from app.models.audit import AuditLog
from app.models.organization import Organization
from app.schemas.marketplace_developer import (
    ApplicationRegister,
    DeveloperOrganizationCreate,
)
from app.services.marketplace_developer import MarketplaceDeveloperService
from app.services.marketplace_sdk import (
    MarketplaceAccessService,
    MarketplaceEventService,
    MarketplaceSdkService,
)
from app.services.rbac import AuthorizationContext
from app.webhooks.events import WEBHOOK_EVENT_TYPES
from tests.conftest import make_user

_DATA_GRANTS = (
    "leads.view",
    "leads.manage",
    "contacts.view",
    "contacts.manage",
    "deals.view",
    "deals.manage",
    "properties.view",
)


def _auth(  # type: ignore[no-untyped-def]
    organization: Organization, user_id, *, manage: bool = True, full: bool = True
) -> AuthorizationContext:
    if not manage:
        grants = {"leads.view": Scope.OWN}
    elif full:
        grants = {"settings.manage": Scope.ALL} | dict.fromkeys(_DATA_GRANTS, Scope.ALL)
    else:
        grants = {"settings.manage": Scope.ALL}
    return AuthorizationContext(
        user_id=user_id,
        organization_id=organization.id,
        role_keys=("admin",),
        grants=grants,
    )


async def _application(db, auth, user, slug: str = "sdk-app"):  # type: ignore[no-untyped-def]
    devsvc = MarketplaceDeveloperService(db, auth)
    developer = await devsvc.create_developer(
        user, DeveloperOrganizationCreate(name="Dev Co")
    )
    return await devsvc.register_application(
        user,
        ApplicationRegister(
            developer_org_id=developer.id,
            name="App",
            slug=slug,
            metadata={"display_name": "App", "summary": "An app."},
        ),
    )


# =============================================================== pure domain


class TestCapabilities:
    def test_registry(self) -> None:
        assert is_known_capability("leads.read")
        assert not is_known_capability("leads.destroy")
        assert sdk_capability("leads.read").required_permission == "leads.view"
        with pytest.raises(KeyError):
            sdk_capability("nope")

    def test_required_permissions(self) -> None:
        assert required_permissions(["leads.read", "deals.write"]) == [
            "deals.manage",
            "leads.view",
        ]

    def test_unsupported(self) -> None:
        assert unsupported_capabilities(["leads.read", "x.y"]) == ["x.y"]


class TestCompatibility:
    def test_compatible(self) -> None:
        report = validate_compatibility("1.0.0", ["leads.read"])
        assert report.compatible is True and report.version_ok is True

    def test_version_out_of_range(self) -> None:
        report = validate_compatibility("2.0.0", ["leads.read"])
        assert report.compatible is False and report.version_ok is False

    def test_unsupported_capability(self) -> None:
        report = validate_compatibility("1.0.0", ["leads.read", "bogus.cap"])
        assert report.compatible is False
        assert report.unsupported_capabilities == ["bogus.cap"]

    def test_invalid_version(self) -> None:
        assert validate_compatibility("nope", []).version_ok is False


# =============================================================== SDK register (db)


class TestSdkRegister:
    async def test_register_and_audit(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "sdk@vantage.example")
        auth = _auth(organization, user.id)
        app = await _application(db, auth, user)

        service = MarketplaceSdkService(db, auth)
        result = await service.register(
            user, app.id, "1.0.0", ["leads.read", "deals.write"]
        )
        assert result.status == "registered"
        assert result.requested_permissions == ["deals.manage", "leads.view"]

        assert (await service.requirements(app.id)).sdk_version == "1.0.0"

        audit = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == "sdk.application.registered")
            )
        ).scalars().all()
        assert len(audit) == 1

    async def test_incompatible_is_refused_and_audited(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "bad@vantage.example")
        auth = _auth(organization, user.id)
        app = await _application(db, auth, user, slug="bad-app")
        service = MarketplaceSdkService(db, auth)

        with pytest.raises(AppError):
            await service.register(user, app.id, "2.0.0", ["leads.read"])
        with pytest.raises(AppError):
            await service.register(user, app.id, "1.0.0", ["bogus.cap"])

        failed = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == "sdk.compatibility.failed")
            )
        ).scalars().all()
        assert len(failed) == 2

    async def test_register_requires_manage(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "nom@vantage.example")
        app = await _application(db, _auth(organization, user.id), user, slug="nom-app")
        service = MarketplaceSdkService(db, _auth(organization, user.id, manage=False))
        with pytest.raises(PermissionDeniedError):
            await service.register(user, app.id, "1.0.0", ["leads.read"])


# =============================================================== access (db)


class TestAccess:
    async def test_grant_revoke_and_audit(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "acc@vantage.example")
        auth = _auth(organization, user.id)
        app = await _application(db, auth, user, slug="acc-app")

        service = MarketplaceAccessService(db, auth)
        grant = await service.grant(user, app.id, ["leads.read", "deals.write"])
        assert grant.active is True
        assert grant.granted_permissions == ["deals.write", "leads.read"]
        assert await service.has_access(app.id, "leads.read") is True
        assert await service.has_access(app.id, "contacts.read") is False

        granted_audit = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == "access.granted")
            )
        ).scalars().all()
        assert len(granted_audit) == 1

        revoked = await service.revoke(user, app.id)
        assert revoked.active is False and revoked.revoked_at is not None
        assert (await service.status(app.id)).active is False
        assert await service.has_access(app.id, "leads.read") is False

    async def test_cannot_grant_permission_not_held(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "rbac@vantage.example")
        # settings.manage only — does NOT hold leads.view.
        auth = _auth(organization, user.id, full=False)
        app = await _application(db, auth, user, slug="rbac-app")
        service = MarketplaceAccessService(db, auth)
        with pytest.raises(PermissionDeniedError):
            await service.grant(user, app.id, ["leads.read"])
        # automation.trigger maps to settings.manage, which the caller holds.
        grant = await service.grant(user, app.id, ["automation.trigger"])
        assert grant.granted_permissions == ["automation.trigger"]

    async def test_invalid_capability_rejected(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "inv@vantage.example")
        auth = _auth(organization, user.id)
        app = await _application(db, auth, user, slug="inv-app")
        with pytest.raises(AppError):
            await MarketplaceAccessService(db, auth).grant(user, app.id, ["nope.cap"])


# =============================================================== events (db)


class TestEvents:
    async def test_subscribe_and_remove(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "evt@vantage.example")
        auth = _auth(organization, user.id)
        app = await _application(db, auth, user, slug="evt-app")

        service = MarketplaceEventService(db, auth)
        assert set(service.available_events()) == set(WEBHOOK_EVENT_TYPES)

        sub = await service.subscribe(user, app.id, "deal.created")
        assert sub.event_name == "deal.created" and sub.enabled is True

        # A duplicate and an unsupported event are both refused.
        with pytest.raises(AppError):
            await service.subscribe(user, app.id, "deal.created")
        with pytest.raises(AppError):
            await service.subscribe(user, app.id, "not.an.event")

        assert [s.event_name for s in await service.list_subscriptions(app.id)] == [
            "deal.created"
        ]

        await service.unsubscribe(user, app.id, "deal.created")
        assert await service.list_subscriptions(app.id) == []

        actions = {
            row.action
            for row in (
                await db.execute(
                    select(AuditLog).where(AuditLog.entity_id == app.id)
                )
            ).scalars().all()
        }
        assert {"event.subscription.created", "event.subscription.removed"} <= actions


# =============================================================== isolation (db)


class TestTenantIsolation:
    async def test_application_invisible_across_tenants(
        self,
        db: AsyncSession,
        organization: Organization,
        other_organization: Organization,
    ) -> None:
        user = await make_user(db, organization, "own@vantage.example")
        auth = _auth(organization, user.id)
        app = await _application(db, auth, user, slug="iso-app")
        await MarketplaceSdkService(db, auth).register(
            user, app.id, "1.0.0", ["leads.read"]
        )

        them = await make_user(db, other_organization, "them@meridian.example")
        them_auth = _auth(other_organization, them.id)
        # A different tenant cannot see or act on the application.
        with pytest.raises(NotFoundError):
            await MarketplaceSdkService(db, them_auth).requirements(app.id)
        with pytest.raises(NotFoundError):
            await MarketplaceAccessService(db, them_auth).grant(
                them, app.id, ["leads.read"]
            )
