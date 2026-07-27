"""Client business logic.

Mirrors `app/services/lead.py` — authorization decisions that need domain
knowledge, audit emission, ownership defaults; no HTTP concerns and no SQL.
Every mutation writes its audit entry in the same transaction as the change, so
a rolled-back action cannot leave a record claiming it happened.

`convert_lead` is the one genuinely new piece of behaviour in this slice.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.emitter import is_automation_actor, record_event, take_snapshot
from app.core.audit_actions import AuditAction
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from app.core.logging import get_logger
from app.core.permissions import Scope
from app.models.client import Client
from app.models.lead import Lead
from app.models.user import User
from app.repositories.client import ClientRepository
from app.repositories.lead import LeadRepository
from app.repositories.user import UserRepository
from app.schemas.client import (
    ClientConvert,
    ClientCreate,
    ClientFilters,
    ClientUpdate,
    has_identity,
)
from app.schemas.common import Cursor
from app.services.audit import AuditService, build_diff
from app.services.rbac import AuthorizationContext, RbacService

logger = get_logger(__name__)

ENTITY_TYPE = "client"

# Fields worth recording a before/after for. Excludes the derived and the
# noisy: `search_vector` is generated, `updated_at` changes on every write.
AUDITED_FIELDS = (
    "first_name",
    "last_name",
    "company_name",
    "email",
    "phone",
    "type",
    "status",
    "lifetime_value",
    "currency",
    "client_since",
    "notes",
    "tags",
    "owner_id",
)


class ClientService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.clients = ClientRepository(session)
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

    async def list_clients(
        self,
        *,
        filters: ClientFilters,
        limit: int,
        cursor: Cursor | None,
        ascending: bool = False,
    ) -> tuple[list[Client], bool]:
        owner_ids = await self._owner_ids("contacts.view")
        return await self.clients.list_page(
            self.auth.organization_id,
            owner_ids=owner_ids,
            filters=filters,
            limit=limit,
            cursor=cursor,
            ascending=ascending,
        )

    async def get_client(self, client_id: UUID) -> Client:
        owner_ids = await self._owner_ids("contacts.view")
        client = await self.clients.get_visible(
            client_id, self.auth.organization_id, owner_ids
        )
        if client is None:
            # 404, not 403. Distinguishing them tells the caller a record
            # exists that they cannot see — an existence oracle.
            raise NotFoundError("Client not found.")
        return client

    async def type_counts(self) -> dict[str, int]:
        owner_ids = await self._owner_ids("contacts.view")
        return await self.clients.count_by_type(self.auth.organization_id, owner_ids)

    # -------------------------------------------------------------- write

    async def create_client(self, payload: ClientCreate, actor: User) -> Client:
        self.auth.require("contacts.manage")

        owner_id = payload.owner_id or actor.id
        if owner_id != actor.id:
            # Assigning to someone else is a distinct capability: an agent may
            # create their own clients without being able to stuff another
            # agent's book.
            await self._assert_can_assign_to(owner_id)

        client = Client(
            organization_id=self.auth.organization_id,
            owner_id=owner_id,
            created_by=actor.id,
            updated_by=actor.id,
            **payload.model_dump(exclude={"owner_id"}),
        )
        await self.clients.add(client)

        await self.audit.record(
            action=AuditAction.RECORD_CREATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=client.id,
            metadata={"type": client.type, "status": client.status},
        )
        await record_event(
            self.session,
            organization_id=self.auth.organization_id,
            event_type="client.created",
            entity_type="client",
            record=client,
            actor_id=actor.id,
            by_automation=is_automation_actor(self.auth.role_keys),
        )
        logger.info("client_created", extra={"client_id": str(client.id)})
        return client

    async def update_client(
        self, client_id: UUID, payload: ClientUpdate, actor: User
    ) -> Client:
        # Load under the *manage* scope, not view: a user who can see a client
        # but not manage it must get 404 here rather than a partial edit.
        owner_ids = await self._owner_ids("contacts.manage")
        client = await self.clients.get_visible(
            client_id, self.auth.organization_id, owner_ids
        )
        if client is None:
            raise NotFoundError("Client not found.")

        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            return client

        if "owner_id" in updates and updates["owner_id"] != client.owner_id:
            await self._assert_can_assign_to(updates["owner_id"])

        # The identity rule spans three columns, so it can only be checked
        # against the merged result — a PATCH clearing `company_name` is fine
        # unless the record has no person name either. The DB CHECK is the
        # backstop; this turns that 500 into a 422-shaped error.
        merged = {
            field: updates.get(field, getattr(client, field))
            for field in ("first_name", "last_name", "company_name")
        }
        if not has_identity(**merged):
            raise ConflictError(
                "A client must keep either a first and last name, or a company name."
            )

        snapshot_before = take_snapshot(ENTITY_TYPE, client)
        before = {field: getattr(client, field) for field in AUDITED_FIELDS}
        for field, value in updates.items():
            setattr(client, field, value)
        client.updated_by = actor.id
        await self.session.flush()

        after = {field: getattr(client, field) for field in AUDITED_FIELDS}
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
                entity_id=client.id,
                metadata={"changes": diff},
            )
            await record_event(
                self.session,
                organization_id=self.auth.organization_id,
                event_type="client.updated",
                entity_type="client",
                record=client,
                actor_id=actor.id,
                previous=snapshot_before,
                by_automation=is_automation_actor(self.auth.role_keys),
            )
            logger.info(
                "client_updated",
                extra={"client_id": str(client.id), "fields": sorted(diff)},
            )
        return await self._reload(client)

    async def delete_client(self, client_id: UUID, actor: User) -> None:
        """Soft delete.

        CRM users expect undo, and retention rules expect the row to survive.
        The audit entry outlives the record either way.
        """
        owner_ids = await self._owner_ids("contacts.manage")
        client = await self.clients.get_visible(
            client_id, self.auth.organization_id, owner_ids
        )
        if client is None:
            raise NotFoundError("Client not found.")

        client.deleted_at = datetime.now(UTC)
        client.updated_by = actor.id
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_DELETED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=client.id,
            metadata={"name": client.display_name, "type": client.type},
        )
        logger.info("client_deleted", extra={"client_id": str(client.id)})

    async def assign_client(
        self, client_id: UUID, owner_id: UUID, actor: User
    ) -> Client:
        """Reassign ownership. Gated on `contacts.assign`, not `contacts.manage`."""
        self.auth.require("contacts.assign")
        await self._assert_can_assign_to(owner_id)

        owner_ids = await self._owner_ids("contacts.manage")
        client = await self.clients.get_visible(
            client_id, self.auth.organization_id, owner_ids
        )
        if client is None:
            raise NotFoundError("Client not found.")

        previous = client.owner_id
        client.owner_id = owner_id
        client.updated_by = actor.id
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_UPDATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=client.id,
            metadata={
                "changes": {
                    "owner_id": {
                        "old": str(previous) if previous else None,
                        "new": str(owner_id),
                    }
                }
            },
        )
        return await self._reload(client)

    # ---------------------------------------------------------- conversion

    async def convert_lead(
        self, lead_id: UUID, payload: ClientConvert, actor: User
    ) -> tuple[Client, Lead]:
        """Turn a lead into a client, transactionally and exactly once.

        Requires **both** `leads.manage` and `contacts.manage`, each resolved to
        its own scope. Requiring only the former would let someone who can edit
        leads mint client records they could not otherwise create; requiring
        only the latter would let them convert a lead they cannot even see.

        The lead is kept rather than deleted: it is the top of the funnel and
        the substrate for Phase 4 conversion reporting. It comes back with
        `status='converted'` and a link to the client it became.
        """
        lead_owner_ids = await self._owner_ids("leads.manage")
        # Resolved for its permission check and its scope, both of which the
        # client insert below depends on.
        await self._owner_ids("contacts.manage")

        # FOR UPDATE, so two concurrent conversions of one lead serialise
        # rather than both passing the converted check below.
        lead = await self.leads.get_visible_for_update(
            lead_id, self.auth.organization_id, lead_owner_ids
        )
        if lead is None:
            raise NotFoundError("Lead not found.")

        if lead.converted_client_id is not None:
            raise ConflictError(
                "This lead has already been converted to a client.",
                client_id=str(lead.converted_client_id),
            )

        client = Client(
            organization_id=self.auth.organization_id,
            # Inherited, not reassigned: whoever worked the lead keeps the
            # relationship. A separate assign call can move it afterwards, and
            # that call is permission-gated in its own right.
            owner_id=lead.owner_id,
            first_name=lead.first_name,
            last_name=lead.last_name,
            company_name=payload.company_name,
            email=lead.email,
            phone=lead.phone,
            type=payload.type,
            status="active",
            currency=lead.currency,
            client_since=payload.client_since or date.today(),
            notes=lead.notes,
            tags=list(lead.tags or []),
            source_lead_id=lead.id,
            created_by=actor.id,
            updated_by=actor.id,
        )
        self.session.add(client)

        try:
            await self.session.flush()
        except IntegrityError as exc:
            # uq_clients_source_lead. Reachable if a concurrent transaction
            # committed between our lock being granted and this flush — the
            # index is the guarantee, the check above is the fast path.
            raise ConflictError(
                "This lead has already been converted to a client."
            ) from exc

        lead.converted_client_id = client.id
        lead.status = "converted"
        lead.converted_at = datetime.now(UTC)
        lead.updated_by = actor.id
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_CREATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=client.id,
            metadata={"type": client.type, "source_lead_id": str(lead.id)},
        )
        # A second entry against the lead, because the funnel event belongs to
        # the lead's history — "this lead converted" is the question Phase 4
        # asks, and it should not have to be inferred from a client insert.
        await self.audit.record(
            action=AuditAction.RECORD_CONVERTED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="lead",
            entity_id=lead.id,
            metadata={"client_id": str(client.id)},
        )
        await record_event(
            self.session,
            organization_id=self.auth.organization_id,
            event_type="client.created",
            entity_type="client",
            record=client,
            actor_id=actor.id,
            extra={"from_lead_id": str(lead.id)},
            by_automation=is_automation_actor(self.auth.role_keys),
        )
        await record_event(
            self.session,
            organization_id=self.auth.organization_id,
            event_type="lead.converted",
            entity_type="lead",
            record=lead,
            actor_id=actor.id,
            extra={"client_id": str(client.id)},
            by_automation=is_automation_actor(self.auth.role_keys),
        )
        logger.info(
            "lead_converted",
            extra={"lead_id": str(lead.id), "client_id": str(client.id)},
        )

        await self.session.refresh(client, ["updated_at"])
        return client, lead

    # ------------------------------------------------------------ helpers

    async def _reload(self, client: Client) -> Client:
        """Re-read server-generated columns after a write.

        `updated_at` has a server-side onupdate and `search_vector` is
        generated, so SQLAlchemy expires both after an UPDATE. Reading either
        while the router serialises the response would trigger a lazy load
        outside the async context — MissingGreenlet, a 500 on every PATCH.
        Refreshing here keeps that IO in the service where it belongs.
        """
        await self.session.refresh(client, ["updated_at"])
        return client

    async def _assert_can_assign_to(self, owner_id: UUID) -> None:
        """The assignee must be a real member of this workspace.

        Without this check an id from another tenant could be written into
        `owner_id`. RLS would then hide the client from everyone, because no
        user in this organization matches — data silently lost rather than
        leaked, but lost all the same.
        """
        target = await self.users.get(owner_id, self.auth.organization_id)
        if target is None:
            raise NotFoundError("Assignee is not a member of this workspace.")

        # Assigning outside your own scope is itself a privilege: an agent
        # cannot hand work to a colleague they have no visibility of.
        scope = self.auth.scope_for("contacts.assign")
        if scope is None:
            raise PermissionDeniedError(
                "This action requires the 'contacts.assign' permission."
            )
        if scope is Scope.OWN and owner_id != self.auth.user_id:
            raise PermissionDeniedError("You can only assign clients to yourself.")
        if scope is Scope.TEAM:
            teammates = await self.rbac.team_member_ids(
                self.auth.user_id, self.auth.organization_id
            )
            if owner_id not in teammates:
                raise PermissionDeniedError(
                    "You can only assign clients within your team."
                )
