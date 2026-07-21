"""Lead business logic.

Reference service for CRM entities. Responsibilities, and what stays out:

  in   — authorization decisions that need domain knowledge, audit emission,
         ownership defaults, transitions
  out  — HTTP status codes, request parsing, SQL

Every mutation writes an audit entry in the same transaction as the change, so
a rolled-back action cannot leave a record claiming it happened.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.core.logging import get_logger
from app.core.permissions import Scope
from app.models.lead import Lead
from app.models.user import User
from app.repositories.lead import LeadRepository
from app.repositories.user import UserRepository
from app.schemas.common import Cursor
from app.schemas.lead import LeadCreate, LeadFilters, LeadUpdate
from app.services.audit import AuditService, build_diff
from app.services.rbac import AuthorizationContext, RbacService

logger = get_logger(__name__)

ENTITY_TYPE = "lead"

# Fields worth recording a before/after for. Excludes the noisy and the
# derived: `search_vector` is generated, `updated_at` changes on every write.
AUDITED_FIELDS = (
    "first_name",
    "last_name",
    "email",
    "phone",
    "stage",
    "status",
    "source",
    "temperature",
    "budget_min",
    "budget_max",
    "currency",
    "preferred_location",
    "notes",
    "tags",
    "owner_id",
)


class LeadService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.leads = LeadRepository(session)
        self.users = UserRepository(session)
        self.audit = AuditService(session)
        self.rbac = RbacService(session)

    # -------------------------------------------------------------- scope

    async def _owner_ids(self, permission: str) -> list[UUID] | None:
        """Resolve a permission's scope into an owner-id predicate.

        Raises 403 if the permission is not held at all — `require` rather than
        a boolean, so a caller cannot forget to check.
        """
        scope = self.auth.require(permission)
        return await self.rbac.owner_ids_for_scope(self.auth, scope)

    # --------------------------------------------------------------- read

    async def list_leads(
        self,
        *,
        filters: LeadFilters,
        limit: int,
        cursor: Cursor | None,
    ) -> tuple[list[Lead], bool]:
        owner_ids = await self._owner_ids("leads.view")
        return await self.leads.list_page(
            self.auth.organization_id,
            owner_ids=owner_ids,
            filters=filters,
            limit=limit,
            cursor=cursor,
        )

    async def get_lead(self, lead_id: UUID) -> Lead:
        owner_ids = await self._owner_ids("leads.view")
        lead = await self.leads.get_visible(
            lead_id, self.auth.organization_id, owner_ids
        )
        if lead is None:
            # 404, not 403. Distinguishing them tells the caller a record
            # exists that they cannot see — an existence oracle.
            raise NotFoundError("Lead not found.")
        return lead

    async def stage_counts(self) -> dict[str, int]:
        owner_ids = await self._owner_ids("leads.view")
        return await self.leads.count_by_stage(self.auth.organization_id, owner_ids)

    # -------------------------------------------------------------- write

    async def create_lead(self, payload: LeadCreate, actor: User) -> Lead:
        self.auth.require("leads.manage")

        owner_id = payload.owner_id or actor.id
        if owner_id != actor.id:
            # Assigning to someone else is a distinct capability: an agent may
            # create their own leads without being able to stuff another
            # agent's pipeline.
            await self._assert_can_assign_to(owner_id)

        lead = Lead(
            organization_id=self.auth.organization_id,
            owner_id=owner_id,
            created_by=actor.id,
            updated_by=actor.id,
            **payload.model_dump(exclude={"owner_id"}),
        )
        await self.leads.add(lead)

        await self.audit.record(
            action=AuditAction.RECORD_CREATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=lead.id,
            metadata={"stage": lead.stage, "source": lead.source},
        )
        logger.info("lead_created", extra={"lead_id": str(lead.id)})
        return lead

    async def update_lead(
        self, lead_id: UUID, payload: LeadUpdate, actor: User
    ) -> Lead:
        # Load under the *manage* scope, not view: a user who can see a lead
        # but not manage it must get 404 here rather than a partial edit.
        owner_ids = await self._owner_ids("leads.manage")
        lead = await self.leads.get_visible(
            lead_id, self.auth.organization_id, owner_ids
        )
        if lead is None:
            raise NotFoundError("Lead not found.")

        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            return lead

        if "owner_id" in updates and updates["owner_id"] != lead.owner_id:
            await self._assert_can_assign_to(updates["owner_id"])

        before = {field: getattr(lead, field) for field in AUDITED_FIELDS}
        for field, value in updates.items():
            setattr(lead, field, value)
        lead.updated_by = actor.id
        await self.session.flush()

        after = {field: getattr(lead, field) for field in AUDITED_FIELDS}
        diff = build_diff(before, after)

        # Only audit an actual change. A PATCH that sets a field to its current
        # value is not an event worth recording.
        if diff:
            await self.audit.record(
                action=AuditAction.RECORD_UPDATED,
                organization_id=self.auth.organization_id,
                actor_id=actor.id,
                actor_email=actor.email,
                entity_type=ENTITY_TYPE,
                entity_id=lead.id,
                metadata={"changes": diff},
            )
            logger.info(
                "lead_updated",
                extra={"lead_id": str(lead.id), "fields": sorted(diff)},
            )
        return await self._reload(lead)

    async def delete_lead(self, lead_id: UUID, actor: User) -> None:
        """Soft delete.

        CRM users expect undo, and retention rules expect the row to survive.
        The audit entry outlives the record either way.
        """
        owner_ids = await self._owner_ids("leads.manage")
        lead = await self.leads.get_visible(
            lead_id, self.auth.organization_id, owner_ids
        )
        if lead is None:
            raise NotFoundError("Lead not found.")

        from datetime import UTC, datetime

        lead.deleted_at = datetime.now(UTC)
        lead.updated_by = actor.id
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_DELETED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=lead.id,
            metadata={"name": lead.full_name, "stage": lead.stage},
        )
        logger.info("lead_deleted", extra={"lead_id": str(lead.id)})

    async def assign_lead(self, lead_id: UUID, owner_id: UUID, actor: User) -> Lead:
        """Reassign ownership. Gated on `leads.assign`, not `leads.manage`."""
        self.auth.require("leads.assign")
        await self._assert_can_assign_to(owner_id)

        owner_ids = await self._owner_ids("leads.manage")
        lead = await self.leads.get_visible(
            lead_id, self.auth.organization_id, owner_ids
        )
        if lead is None:
            raise NotFoundError("Lead not found.")

        previous = lead.owner_id
        lead.owner_id = owner_id
        lead.updated_by = actor.id
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_UPDATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=lead.id,
            metadata={
                "changes": {
                    "owner_id": {
                        "old": str(previous) if previous else None,
                        "new": str(owner_id),
                    }
                }
            },
        )
        return await self._reload(lead)


    async def _reload(self, lead: Lead) -> Lead:
        """Re-read server-generated columns after a write.

        `updated_at` has a server-side onupdate, and `search_vector` is a
        generated column. SQLAlchemy marks both expired after an UPDATE, so the
        next attribute access triggers a lazy load — which raises
        MissingGreenlet once we are outside the async context, i.e. while the
        router is serialising the response.

        Refreshing here, inside the service, keeps that IO where it belongs.
        """
        await self.session.refresh(lead, ["updated_at"])
        return lead

    # ------------------------------------------------------------ helpers

    async def _assert_can_assign_to(self, owner_id: UUID) -> None:
        """The assignee must be a real member of this workspace.

        Without this check an id from another tenant could be written into
        `owner_id`. RLS would then hide the lead from everyone, because no user
        in this organization matches — data silently lost rather than leaked,
        but lost all the same.
        """
        target = await self.users.get(owner_id, self.auth.organization_id)
        if target is None:
            raise NotFoundError("Assignee is not a member of this workspace.")

        # Assigning outside your own scope is itself a privilege: an agent
        # cannot hand work to a colleague they have no visibility of.
        scope = self.auth.scope_for("leads.assign")
        if scope is None:
            raise PermissionDeniedError(
                "This action requires the 'leads.assign' permission."
            )
        if scope is Scope.OWN and owner_id != self.auth.user_id:
            raise PermissionDeniedError("You can only assign leads to yourself.")
        if scope is Scope.TEAM:
            teammates = await self.rbac.team_member_ids(
                self.auth.user_id, self.auth.organization_id
            )
            if owner_id not in teammates:
                raise PermissionDeniedError(
                    "You can only assign leads within your team."
                )
