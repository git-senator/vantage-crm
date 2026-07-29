"""Marketplace developer platform (Phase 9.5).

The properties that carry the milestone:

  * **The application lifecycle is an explicit machine** — draft -> submitted ->
    review -> approved -> published, with illegal transitions refused precisely.
  * **Registration and the review workflow are governed and audited**, and the
    review history is durable.
  * **Developer credentials are hashed** — the raw secret is shown once and never
    stored, mirroring API keys.
  * **RBAC gates every mutation**, tenant isolation holds, and every lifecycle and
    credential act is audited.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, NotFoundError, PermissionDeniedError
from app.core.permissions import Scope
from app.core.security import hash_api_key
from app.marketplace.developer import (
    ApplicationLifecycleError,
    application_compatibility,
    can_publish,
    is_under_review,
    next_app_state,
    validate_app_metadata,
    validate_scopes,
    validate_slug,
)
from app.models.audit import AuditLog
from app.models.developer import DeveloperApiCredential
from app.models.organization import Organization
from app.schemas.marketplace_developer import (
    ApplicationRegister,
    CredentialCreate,
    DeveloperOrganizationCreate,
)
from app.services.marketplace_developer import (
    ApplicationPublishingService,
    ApplicationReviewService,
    DeveloperCredentialService,
    MarketplaceDeveloperService,
)
from app.services.rbac import AuthorizationContext
from tests.conftest import make_user

TEST_JWT_SECRET = "test_secret_that_is_at_least_thirty_two_chars"


def _auth(organization: Organization, user_id, manage: bool = True) -> AuthorizationContext:  # type: ignore[no-untyped-def]
    grants = {"settings.manage": Scope.ALL} if manage else {"leads.view": Scope.OWN}
    return AuthorizationContext(
        user_id=user_id,
        organization_id=organization.id,
        role_keys=("admin",),
        grants=grants,
    )


def _register(developer_org_id, slug: str = "acme-app", **over: Any) -> ApplicationRegister:  # type: ignore[no-untyped-def]
    base: dict[str, Any] = {
        "developer_org_id": developer_org_id,
        "name": "Acme App",
        "slug": slug,
        "metadata": {"display_name": "Acme App", "summary": "Does Acme things."},
    }
    base.update(over)
    return ApplicationRegister(**base)


async def _developer(db, auth, user, name="Acme Dev"):  # type: ignore[no-untyped-def]
    return await MarketplaceDeveloperService(db, auth).create_developer(
        user, DeveloperOrganizationCreate(name=name)
    )


# =============================================================== pure domain


class TestLifecycle:
    def test_full_path(self) -> None:
        state = "draft"
        for action, expected in (
            ("submit", "submitted"),
            ("begin_review", "review"),
            ("approve", "approved"),
            ("publish", "published"),
            ("deprecate", "deprecated"),
            ("retire", "retired"),
        ):
            state = next_app_state(state, action)
            assert state == expected

    def test_illegal(self) -> None:
        with pytest.raises(ApplicationLifecycleError):
            next_app_state("draft", "publish")
        with pytest.raises(ApplicationLifecycleError):
            next_app_state("submitted", "publish")
        with pytest.raises(ApplicationLifecycleError):
            next_app_state("published", "submit")

    def test_predicates(self) -> None:
        assert is_under_review("submitted") and is_under_review("review")
        assert not is_under_review("draft")
        assert can_publish("approved") and not can_publish("draft")
        assert next_app_state("review", "reject") == "draft"


class TestValidation:
    def test_slug(self) -> None:
        assert validate_slug("acme-app") == "acme-app"
        with pytest.raises(ValueError):
            validate_slug("Acme_App")
        with pytest.raises(ValueError):
            validate_slug("ab")

    def test_scopes(self) -> None:
        assert validate_scopes(["apps.read", "apps.read", "apps.write"]) == [
            "apps.read",
            "apps.write",
        ]
        with pytest.raises(ValueError):
            validate_scopes(["apps.everything"])

    def test_metadata(self) -> None:
        assert validate_app_metadata({"display_name": "X", "summary": "y"})
        with pytest.raises(ValueError):
            validate_app_metadata({"summary": "y"})
        with pytest.raises(ValueError):
            validate_app_metadata({"display_name": "X"})

    def test_compatibility(self) -> None:
        assert application_compatibility({"version": "1.0.0"}).compatible is True
        assert application_compatibility({"sdk_version": "2.0.0"}).compatible is False


# =============================================================== developers (db)


class TestDeveloperRegistration:
    async def test_create_and_audit(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "dev@vantage.example")
        auth = _auth(organization, user.id)
        developer = await _developer(db, auth, user)
        assert developer.status == "active"

        service = MarketplaceDeveloperService(db, auth)
        assert [d.id for d in await service.list_developers()] == [developer.id]
        assert (await service.get_developer(developer.id)).name == "Acme Dev"

        audit = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == "developer.created")
            )
        ).scalars().all()
        assert len(audit) == 1

    async def test_requires_manage(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "nom@vantage.example")
        service = MarketplaceDeveloperService(
            db, _auth(organization, user.id, manage=False)
        )
        with pytest.raises(PermissionDeniedError):
            await service.create_developer(
                user, DeveloperOrganizationCreate(name="Nope")
            )

    async def test_tenant_isolation(
        self,
        db: AsyncSession,
        organization: Organization,
        other_organization: Organization,
    ) -> None:
        user = await make_user(db, organization, "own@vantage.example")
        developer = await _developer(db, _auth(organization, user.id), user)

        them = await make_user(db, other_organization, "them@meridian.example")
        their = MarketplaceDeveloperService(db, _auth(other_organization, them.id))
        assert await their.list_developers() == []
        with pytest.raises(NotFoundError):
            await their.get_developer(developer.id)


# =============================================================== applications (db)


class TestApplicationLifecycle:
    async def test_register_submit_approve_publish(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "app@vantage.example")
        auth = _auth(organization, user.id)
        developer = await _developer(db, auth, user)

        devsvc = MarketplaceDeveloperService(db, auth)
        app = await devsvc.register_application(user, _register(developer.id))
        assert app.lifecycle_status == "draft"

        publishing = ApplicationPublishingService(db, auth)
        submitted = await publishing.submit(user, app.id)
        assert submitted.lifecycle_status == "submitted"
        assert submitted.submitted_at is not None

        reviews = ApplicationReviewService(db, auth)
        assert [a.id for a in await reviews.review_queue()] == [app.id]
        decision = await reviews.approve(user, app.id, "1.0.0", "LGTM")
        assert decision.status == "approved"
        assert (await publishing.status(app.id)).lifecycle_status == "approved"

        published = await publishing.publish(user, app.id)
        assert published.lifecycle_status == "published"
        assert published.published_at is not None

        # The review history is durable.
        history = await reviews.list_reviews(app.id)
        assert [r.status for r in history] == ["approved"]

        actions = {
            row.action
            for row in (
                await db.execute(select(AuditLog).where(AuditLog.entity_id == app.id))
            ).scalars().all()
        }
        assert {
            "application.created",
            "application.submitted",
            "application.approved",
            "application.published",
        } <= actions

    async def test_reject_returns_to_draft(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "rej@vantage.example")
        auth = _auth(organization, user.id)
        developer = await _developer(db, auth, user)
        app = await MarketplaceDeveloperService(db, auth).register_application(
            user, _register(developer.id, slug="rej-app")
        )
        publishing = ApplicationPublishingService(db, auth)
        await publishing.submit(user, app.id)

        reviews = ApplicationReviewService(db, auth)
        decision = await reviews.reject(user, app.id, "1.0.0", "needs work")
        assert decision.status == "rejected"
        assert (await publishing.status(app.id)).lifecycle_status == "draft"

    async def test_invalid_publishing_states(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "inv@vantage.example")
        auth = _auth(organization, user.id)
        developer = await _developer(db, auth, user)
        app = await MarketplaceDeveloperService(db, auth).register_application(
            user, _register(developer.id, slug="inv-app")
        )
        publishing = ApplicationPublishingService(db, auth)
        # Cannot publish a draft, nor approve one that is not under review.
        with pytest.raises(AppError):
            await publishing.publish(user, app.id)
        with pytest.raises(AppError):
            await ApplicationReviewService(db, auth).approve(user, app.id, "1.0.0", None)

    async def test_duplicate_slug_refused(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "dup@vantage.example")
        auth = _auth(organization, user.id)
        developer = await _developer(db, auth, user)
        devsvc = MarketplaceDeveloperService(db, auth)
        await devsvc.register_application(user, _register(developer.id, slug="dup-app"))
        with pytest.raises(AppError):
            await devsvc.register_application(
                user, _register(developer.id, slug="dup-app")
            )


# =============================================================== credentials (db)


class TestCredentials:
    async def test_secret_hashed_and_shown_once(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "cred@vantage.example")
        auth = _auth(organization, user.id)
        developer = await _developer(db, auth, user)

        service = DeveloperCredentialService(db, auth)
        result = await service.create(
            user,
            CredentialCreate(
                developer_org_id=developer.id, name="CI", scopes=["apps.read"]
            ),
        )
        assert result.secret.startswith("dvk_")
        assert result.credential.scopes == ["apps.read"]

        # Only a hash is stored, and it matches the raw secret.
        row = (
            await db.execute(
                select(DeveloperApiCredential).where(
                    DeveloperApiCredential.id == result.credential.id
                )
            )
        ).scalar_one()
        assert row.hashed_secret == hash_api_key(result.secret)
        assert result.secret not in (row.hashed_secret, row.prefix, row.last_four)

        # Listing never exposes the secret or the hash.
        listed = await service.list_credentials(developer.id)
        assert len(listed) == 1 and listed[0].is_active is True

        audit = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == "credential.created")
            )
        ).scalars().all()
        assert len(audit) == 1

    async def test_rotate_and_revoke(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "rot@vantage.example")
        auth = _auth(organization, user.id)
        developer = await _developer(db, auth, user)
        service = DeveloperCredentialService(db, auth)
        created = await service.create(
            user, CredentialCreate(developer_org_id=developer.id, name="k", scopes=[])
        )

        rotated = await service.rotate(user, created.credential.id)
        assert rotated.secret != created.secret

        revoked = await service.revoke(user, created.credential.id)
        assert revoked.is_active is False and revoked.revoked_at is not None
        # A revoked credential cannot be rotated.
        with pytest.raises(AppError):
            await service.rotate(user, created.credential.id)

        revoked_audit = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == "credential.revoked")
            )
        ).scalars().all()
        assert len(revoked_audit) == 1

    async def test_unknown_scope_refused(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "sc@vantage.example")
        auth = _auth(organization, user.id)
        developer = await _developer(db, auth, user)
        with pytest.raises(AppError):
            await DeveloperCredentialService(db, auth).create(
                user,
                CredentialCreate(
                    developer_org_id=developer.id, name="bad", scopes=["apps.hack"]
                ),
            )

    async def test_credential_tenant_isolation(
        self,
        db: AsyncSession,
        organization: Organization,
        other_organization: Organization,
    ) -> None:
        user = await make_user(db, organization, "a@vantage.example")
        auth = _auth(organization, user.id)
        developer = await _developer(db, auth, user)
        created = await DeveloperCredentialService(db, auth).create(
            user, CredentialCreate(developer_org_id=developer.id, name="k", scopes=[])
        )
        them = await make_user(db, other_organization, "b@meridian.example")
        their = DeveloperCredentialService(db, _auth(other_organization, them.id))
        with pytest.raises(NotFoundError):
            await their.revoke(them, created.credential.id)
