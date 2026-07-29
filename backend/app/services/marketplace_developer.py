"""Marketplace developer platform services (Phase 9.5).

The developer ecosystem over the marketplace. Four services:

  * ``MarketplaceDeveloperService`` — developer organizations and application
    registration.
  * ``ApplicationPublishingService`` — the publication lifecycle
    (submit/publish/deprecate/retire).
  * ``ApplicationReviewService`` — the review queue and approve/reject decisions,
    with a durable review history.
  * ``DeveloperCredentialService`` — scoped developer API credentials, stored as a
    SHA-256 hash and shown once, mirroring the API-key discipline.

Runtime is reused, not restated: an application wraps a plugin from the plugin
runtime, compatibility is judged by the SDK layer, and lifecycle transitions are
the pure developer domain's. Every mutation is gated on ``settings.manage`` and
runs under RLS; every lifecycle transition and credential act is audited.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.exceptions import AppError, NotFoundError
from app.core.security import hash_api_key
from app.marketplace.developer import (
    ApplicationLifecycleError,
    is_under_review,
    next_app_state,
    validate_app_metadata,
    validate_scopes,
    validate_slug,
)
from app.models.developer import (
    ApplicationVersionReview,
    DeveloperApiCredential,
    DeveloperOrganization,
    MarketplaceApplication,
)
from app.models.user import User
from app.repositories.developer import (
    ApplicationVersionReviewRepository,
    DeveloperApiCredentialRepository,
    DeveloperOrganizationRepository,
    MarketplaceApplicationRepository,
)
from app.repositories.plugin import PluginRepository
from app.schemas.marketplace_developer import (
    ApplicationRead,
    ApplicationRegister,
    ApplicationReviewRead,
    CredentialCreate,
    CredentialCreateResult,
    CredentialRead,
    DeveloperOrganizationCreate,
    DeveloperOrganizationRead,
)
from app.services.audit import AuditService
from app.services.rbac import AuthorizationContext

MANAGE_PERMISSION = "settings.manage"


def _now() -> datetime:
    return datetime.now(UTC)


def _generate_secret() -> tuple[str, str, str, str]:
    """A fresh developer credential: raw secret, its hash, prefix, last four."""
    raw = f"dvk_{secrets.token_urlsafe(32)}"
    return raw, hash_api_key(raw), raw[:12], raw[-4:]


def _developer_read(row: DeveloperOrganization) -> DeveloperOrganizationRead:
    return DeveloperOrganizationRead(
        id=row.id,
        name=row.name,
        contact_email=row.contact_email,
        description=row.description,
        status=row.status,
        created_at=row.created_at,
    )


def _application_read(row: MarketplaceApplication) -> ApplicationRead:
    return ApplicationRead(
        id=row.id,
        developer_org_id=row.developer_org_id,
        plugin_id=row.plugin_id,
        name=row.name,
        slug=row.slug,
        metadata=dict(row.app_metadata),
        lifecycle_status=row.lifecycle_status,
        submitted_at=row.submitted_at,
        approved_at=row.approved_at,
        published_at=row.published_at,
        created_at=row.created_at,
    )


def _review_read(row: ApplicationVersionReview) -> ApplicationReviewRead:
    return ApplicationReviewRead(
        id=row.id,
        application_id=row.application_id,
        version=row.version,
        status=row.status,
        notes=row.notes,
        reviewer_id=row.reviewer_id,
        created_at=row.created_at,
    )


def _credential_read(row: DeveloperApiCredential) -> CredentialRead:
    return CredentialRead(
        id=row.id,
        developer_org_id=row.developer_org_id,
        name=row.name,
        prefix=row.prefix,
        last_four=row.last_four,
        scopes=list(row.scopes),
        is_active=row.is_active,
        revoked_at=row.revoked_at,
        created_at=row.created_at,
    )


# =========================================================== developers & apps


class MarketplaceDeveloperService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.developers = DeveloperOrganizationRepository(session)
        self.applications = MarketplaceApplicationRepository(session)
        self.plugins = PluginRepository(session)
        self.audit = AuditService(session)

    async def create_developer(
        self, actor: User, payload: DeveloperOrganizationCreate
    ) -> DeveloperOrganizationRead:
        self.auth.require(MANAGE_PERMISSION)
        developer = DeveloperOrganization(
            organization_id=self.auth.organization_id,
            created_by=actor.id,
            name=payload.name,
            contact_email=payload.contact_email,
            description=payload.description,
            status="active",
        )
        self.session.add(developer)
        await self.session.flush()
        await self.session.refresh(developer)
        await self.audit.record(
            action=AuditAction.DEVELOPER_CREATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="developer_organization",
            entity_id=developer.id,
            metadata={"name": developer.name},
        )
        return _developer_read(developer)

    async def get_developer(self, developer_org_id: UUID) -> DeveloperOrganizationRead:
        self.auth.require(MANAGE_PERMISSION)
        return _developer_read(await self._load_developer(developer_org_id))

    async def list_developers(self) -> list[DeveloperOrganizationRead]:
        self.auth.require(MANAGE_PERMISSION)
        rows = await self.developers.list_for_org(self.auth.organization_id)
        return [_developer_read(row) for row in rows]

    async def register_application(
        self, actor: User, payload: ApplicationRegister
    ) -> ApplicationRead:
        self.auth.require(MANAGE_PERMISSION)
        developer = await self._load_developer(payload.developer_org_id)
        try:
            validate_slug(payload.slug)
            validate_app_metadata(payload.metadata)
        except ValueError as exc:
            raise AppError(str(exc)) from exc
        if await self.applications.get_by_slug(
            self.auth.organization_id, developer.id, payload.slug
        ):
            raise AppError(f"An application with slug '{payload.slug}' already exists.")
        if payload.plugin_id is not None and not await self.plugins.get_visible(
            payload.plugin_id, self.auth.organization_id
        ):
            raise NotFoundError("Plugin not found.")

        application = MarketplaceApplication(
            organization_id=self.auth.organization_id,
            created_by=actor.id,
            developer_org_id=developer.id,
            plugin_id=payload.plugin_id,
            name=payload.name,
            slug=payload.slug,
            app_metadata=dict(payload.metadata),
            lifecycle_status="draft",
        )
        self.session.add(application)
        await self.session.flush()
        await self.session.refresh(application)
        await self.audit.record(
            action=AuditAction.APPLICATION_CREATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="marketplace_application",
            entity_id=application.id,
            metadata={"slug": application.slug, "developer": str(developer.id)},
        )
        return _application_read(application)

    async def list_applications(
        self, developer_org_id: UUID
    ) -> list[ApplicationRead]:
        self.auth.require(MANAGE_PERMISSION)
        await self._load_developer(developer_org_id)
        rows = await self.applications.list_for_developer(
            self.auth.organization_id, developer_org_id
        )
        return [_application_read(row) for row in rows]

    async def _load_developer(
        self, developer_org_id: UUID
    ) -> DeveloperOrganization:
        developer = await self.developers.get(
            developer_org_id, self.auth.organization_id
        )
        if developer is None:
            raise NotFoundError("Developer organization not found.")
        return developer


# =========================================================== publishing


class ApplicationPublishingService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.applications = MarketplaceApplicationRepository(session)
        self.audit = AuditService(session)

    async def submit(self, actor: User, application_id: UUID) -> ApplicationRead:
        application = await self._load(application_id)
        try:
            validate_app_metadata(application.app_metadata)
        except ValueError as exc:
            raise AppError(str(exc)) from exc
        application.lifecycle_status = self._transition(application, "submit")
        application.submitted_at = _now()
        await self.session.flush()
        await self.session.refresh(application)
        await self._audit(AuditAction.APPLICATION_SUBMITTED, actor, application)
        return _application_read(application)

    async def publish(self, actor: User, application_id: UUID) -> ApplicationRead:
        application = await self._load(application_id)
        application.lifecycle_status = self._transition(application, "publish")
        application.published_at = _now()
        await self.session.flush()
        await self.session.refresh(application)
        await self._audit(AuditAction.APPLICATION_PUBLISHED, actor, application)
        return _application_read(application)

    async def deprecate(self, actor: User, application_id: UUID) -> ApplicationRead:
        application = await self._load(application_id)
        application.lifecycle_status = self._transition(application, "deprecate")
        await self.session.flush()
        await self.session.refresh(application)
        await self._audit(AuditAction.APPLICATION_DEPRECATED, actor, application)
        return _application_read(application)

    async def retire(self, actor: User, application_id: UUID) -> ApplicationRead:
        application = await self._load(application_id)
        application.lifecycle_status = self._transition(application, "retire")
        await self.session.flush()
        await self.session.refresh(application)
        await self._audit(AuditAction.APPLICATION_RETIRED, actor, application)
        return _application_read(application)

    async def status(self, application_id: UUID) -> ApplicationRead:
        return _application_read(await self._load(application_id))

    def _transition(self, application: MarketplaceApplication, action: str) -> str:
        try:
            return next_app_state(application.lifecycle_status, action)
        except ApplicationLifecycleError as exc:
            raise AppError(str(exc)) from exc

    async def _load(self, application_id: UUID) -> MarketplaceApplication:
        self.auth.require(MANAGE_PERMISSION)
        application = await self.applications.get(
            application_id, self.auth.organization_id
        )
        if application is None:
            raise NotFoundError("Application not found.")
        return application

    async def _audit(
        self, action: str, actor: User, application: MarketplaceApplication
    ) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="marketplace_application",
            entity_id=application.id,
            metadata={"slug": application.slug, "status": application.lifecycle_status},
        )


# =========================================================== review


class ApplicationReviewService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.applications = MarketplaceApplicationRepository(session)
        self.reviews = ApplicationVersionReviewRepository(session)
        self.audit = AuditService(session)

    async def review_queue(self) -> list[ApplicationRead]:
        self.auth.require(MANAGE_PERMISSION)
        rows = await self.applications.review_queue(self.auth.organization_id)
        return [_application_read(row) for row in rows]

    async def approve(
        self, actor: User, application_id: UUID, version: str, notes: str | None
    ) -> ApplicationReviewRead:
        application = await self._load_reviewable(application_id)
        # Advance submitted -> review before the decision, so the pipeline state
        # is honest even when a reviewer approves straight from the queue.
        if application.lifecycle_status == "submitted":
            application.lifecycle_status = "review"
        application.lifecycle_status = self._transition(application, "approve")
        application.approved_at = _now()
        review = await self._record(application, version, "approved", notes, actor)
        await self._audit(
            AuditAction.APPLICATION_APPROVED, actor, application, version
        )
        return review

    async def reject(
        self, actor: User, application_id: UUID, version: str, notes: str | None
    ) -> ApplicationReviewRead:
        application = await self._load_reviewable(application_id)
        if application.lifecycle_status == "submitted":
            application.lifecycle_status = "review"
        application.lifecycle_status = self._transition(application, "reject")
        review = await self._record(application, version, "rejected", notes, actor)
        await self._audit(
            AuditAction.APPLICATION_REJECTED, actor, application, version
        )
        return review

    async def list_reviews(
        self, application_id: UUID
    ) -> list[ApplicationReviewRead]:
        self.auth.require(MANAGE_PERMISSION)
        await self._load(application_id)
        rows = await self.reviews.list_for_application(
            self.auth.organization_id, application_id
        )
        return [_review_read(row) for row in rows]

    async def _record(
        self,
        application: MarketplaceApplication,
        version: str,
        status: str,
        notes: str | None,
        actor: User,
    ) -> ApplicationReviewRead:
        review = ApplicationVersionReview(
            organization_id=self.auth.organization_id,
            application_id=application.id,
            reviewer_id=actor.id,
            version=version,
            status=status,
            notes=notes,
        )
        self.session.add(review)
        await self.session.flush()
        await self.session.refresh(review)
        return _review_read(review)

    def _transition(self, application: MarketplaceApplication, action: str) -> str:
        try:
            return next_app_state(application.lifecycle_status, action)
        except ApplicationLifecycleError as exc:
            raise AppError(str(exc)) from exc

    async def _load(self, application_id: UUID) -> MarketplaceApplication:
        application = await self.applications.get(
            application_id, self.auth.organization_id
        )
        if application is None:
            raise NotFoundError("Application not found.")
        return application

    async def _load_reviewable(
        self, application_id: UUID
    ) -> MarketplaceApplication:
        self.auth.require(MANAGE_PERMISSION)
        application = await self._load(application_id)
        if not is_under_review(application.lifecycle_status):
            raise AppError(
                f"An application that is '{application.lifecycle_status}' "
                "is not awaiting review."
            )
        return application

    async def _audit(
        self,
        action: str,
        actor: User,
        application: MarketplaceApplication,
        version: str,
    ) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="marketplace_application",
            entity_id=application.id,
            metadata={"slug": application.slug, "version": version},
        )


# =========================================================== credentials


class DeveloperCredentialService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.developers = DeveloperOrganizationRepository(session)
        self.credentials = DeveloperApiCredentialRepository(session)
        self.audit = AuditService(session)

    async def create(
        self, actor: User, payload: CredentialCreate
    ) -> CredentialCreateResult:
        self.auth.require(MANAGE_PERMISSION)
        developer = await self._load_developer(payload.developer_org_id)
        try:
            scopes = validate_scopes(payload.scopes)
        except ValueError as exc:
            raise AppError(str(exc)) from exc

        raw, hashed, prefix, last_four = _generate_secret()
        credential = DeveloperApiCredential(
            organization_id=self.auth.organization_id,
            created_by=actor.id,
            developer_org_id=developer.id,
            name=payload.name,
            hashed_secret=hashed,
            prefix=prefix,
            last_four=last_four,
            scopes=scopes,
        )
        self.session.add(credential)
        await self.session.flush()
        await self.session.refresh(credential)
        await self._audit(AuditAction.CREDENTIAL_CREATED, actor, credential)
        return CredentialCreateResult(
            credential=_credential_read(credential), secret=raw
        )

    async def rotate(
        self, actor: User, credential_id: UUID
    ) -> CredentialCreateResult:
        self.auth.require(MANAGE_PERMISSION)
        credential = await self._load(credential_id)
        if credential.revoked_at is not None:
            raise AppError("A revoked credential cannot be rotated.")
        raw, hashed, prefix, last_four = _generate_secret()
        credential.hashed_secret = hashed
        credential.prefix = prefix
        credential.last_four = last_four
        await self.session.flush()
        await self.session.refresh(credential)
        await self._audit(AuditAction.CREDENTIAL_CREATED, actor, credential)
        return CredentialCreateResult(
            credential=_credential_read(credential), secret=raw
        )

    async def revoke(self, actor: User, credential_id: UUID) -> CredentialRead:
        self.auth.require(MANAGE_PERMISSION)
        credential = await self._load(credential_id)
        if credential.revoked_at is None:
            credential.revoked_at = _now()
            await self.session.flush()
            await self.session.refresh(credential)
        await self._audit(AuditAction.CREDENTIAL_REVOKED, actor, credential)
        return _credential_read(credential)

    async def list_credentials(
        self, developer_org_id: UUID
    ) -> list[CredentialRead]:
        self.auth.require(MANAGE_PERMISSION)
        await self._load_developer(developer_org_id)
        rows = await self.credentials.list_for_developer(
            self.auth.organization_id, developer_org_id
        )
        return [_credential_read(row) for row in rows]

    async def _load_developer(
        self, developer_org_id: UUID
    ) -> DeveloperOrganization:
        developer = await self.developers.get(
            developer_org_id, self.auth.organization_id
        )
        if developer is None:
            raise NotFoundError("Developer organization not found.")
        return developer

    async def _load(self, credential_id: UUID) -> DeveloperApiCredential:
        credential = await self.credentials.get(
            credential_id, self.auth.organization_id
        )
        if credential is None:
            raise NotFoundError("Credential not found.")
        return credential

    async def _audit(
        self, action: str, actor: User, credential: DeveloperApiCredential
    ) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="developer_api_credential",
            entity_id=credential.id,
            metadata={"name": credential.name, "developer": str(credential.developer_org_id)},
        )


__all__ = [
    "MANAGE_PERMISSION",
    "ApplicationPublishingService",
    "ApplicationReviewService",
    "DeveloperCredentialService",
    "MarketplaceDeveloperService",
]
