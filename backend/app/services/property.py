"""Property business logic.

Mirrors `app/services/lead.py` — authorization decisions that need domain
knowledge, audit emission, assignment defaults; no HTTP concerns and no SQL.
Every mutation writes its audit entry in the same transaction as the change.

Two things differ from leads and clients, both because listings are shared
inventory rather than a personal book of business. See `_load_for_write` and
`_assert_can_assign_to`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.emitter import is_automation_actor, record_event, take_snapshot
from app.core.audit_actions import AuditAction
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from app.core.logging import get_logger
from app.core.permissions import Scope
from app.models.property import Property
from app.models.user import User
from app.repositories.client import ClientRepository
from app.repositories.property import PropertyRepository
from app.repositories.user import UserRepository
from app.schemas.common import Cursor
from app.schemas.property import PropertyCreate, PropertyFilters, PropertyUpdate
from app.services.audit import AuditService, build_diff
from app.services.rbac import AuthorizationContext, RbacService

logger = get_logger(__name__)

ENTITY_TYPE = "property"

# Fields worth recording a before/after for. Excludes the derived and the
# noisy: `search_vector` is generated, `updated_at` changes on every write,
# and the engagement counters are written by tracking rather than by a person.
AUDITED_FIELDS = (
    "title",
    "mls_number",
    "status",
    "property_type",
    "address_line1",
    "city",
    "state",
    "postal_code",
    "price",
    "currency",
    "bedrooms",
    "bathrooms",
    "square_feet",
    "year_built",
    "listed_at",
    "client_id",
    "listing_agent_id",
)


class PropertyService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.properties = PropertyRepository(session)
        self.clients = ClientRepository(session)
        self.users = UserRepository(session)
        self.audit = AuditService(session)
        self.rbac = RbacService(session)

    # -------------------------------------------------------------- scope

    async def _agent_ids(self, permission: str) -> list[UUID] | None:
        """Resolve a permission's scope into a listing-agent predicate.

        Raises 403 if the permission is not held at all — `require` rather than
        a boolean, so a caller cannot forget to check.
        """
        scope = self.auth.require(permission)
        return await self.rbac.owner_ids_for_scope(self.auth, scope)

    # --------------------------------------------------------------- read

    async def list_properties(
        self,
        *,
        filters: PropertyFilters,
        limit: int,
        cursor: Cursor | None,
        ascending: bool = False,
    ) -> tuple[list[Property], bool]:
        agent_ids = await self._agent_ids("properties.view")
        return await self.properties.list_page(
            self.auth.organization_id,
            agent_ids=agent_ids,
            filters=filters,
            limit=limit,
            cursor=cursor,
            ascending=ascending,
        )

    async def get_property(self, property_id: UUID) -> Property:
        agent_ids = await self._agent_ids("properties.view")
        listing = await self.properties.get_visible(
            property_id, self.auth.organization_id, agent_ids
        )
        if listing is None:
            raise NotFoundError("Property not found.")
        return listing

    async def status_counts(self) -> dict[str, int]:
        agent_ids = await self._agent_ids("properties.view")
        return await self.properties.count_by_status(
            self.auth.organization_id, agent_ids
        )

    # -------------------------------------------------------------- write

    async def _load_for_write(self, property_id: UUID) -> Property:
        """Load a listing the caller may modify.

        **This is where properties diverge from leads and clients.** Elsewhere a
        record outside the caller's scope is a 404, never a 403, because
        distinguishing them tells the caller a record exists that they cannot
        see — an existence oracle.

        Listings are shared inventory: an agent holds `properties.view` at ALL
        scope, so the record is already in their own list and its existence is
        not a secret. Returning 404 when they try to edit a colleague's listing
        would be actively misleading, so:

          * not visible under *view* scope  -> 404, the record may not exist
          * visible but not under *manage*  -> 403, you cannot edit this one

        The 404 branch still protects cross-tenant and out-of-team records, so
        no oracle is introduced.
        """
        manage_ids = await self._agent_ids("properties.manage")
        listing = await self.properties.get_visible(
            property_id, self.auth.organization_id, manage_ids
        )
        if listing is not None:
            return listing

        # `scope_for`, not `require`: the caller may legitimately lack
        # properties.view, and that must stay a 404 rather than becoming a
        # confusing 403 about a permission they were not exercising.
        view_scope = self.auth.scope_for("properties.view")
        if view_scope is not None:
            view_ids = await self.rbac.owner_ids_for_scope(self.auth, view_scope)
            visible = await self.properties.get_visible(
                property_id, self.auth.organization_id, view_ids
            )
            if visible is not None:
                raise PermissionDeniedError(
                    "This listing belongs to another agent. "
                    "You can view it, but not change it."
                )

        raise NotFoundError("Property not found.")

    async def create_property(self, payload: PropertyCreate, actor: User) -> Property:
        self.auth.require("properties.manage")

        agent_id = payload.listing_agent_id or actor.id
        if agent_id != actor.id:
            await self._assert_can_assign_to(agent_id)

        if payload.client_id is not None:
            await self._assert_client_exists(payload.client_id)

        listing = Property(
            organization_id=self.auth.organization_id,
            listing_agent_id=agent_id,
            created_by=actor.id,
            updated_by=actor.id,
            **payload.model_dump(exclude={"listing_agent_id"}),
        )
        self.session.add(listing)
        await self._flush_checking_mls()

        await self.audit.record(
            action=AuditAction.RECORD_CREATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=listing.id,
            metadata={"status": listing.status, "type": listing.property_type},
        )
        await record_event(
            self.session,
            organization_id=self.auth.organization_id,
            event_type="property.created",
            entity_type="property",
            record=listing,
            actor_id=actor.id,
            by_automation=is_automation_actor(self.auth.role_keys),
        )
        logger.info("property_created", extra={"property_id": str(listing.id)})
        return listing

    async def update_property(
        self, property_id: UUID, payload: PropertyUpdate, actor: User
    ) -> Property:
        listing = await self._load_for_write(property_id)

        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            return listing

        if (
            "listing_agent_id" in updates
            and updates["listing_agent_id"] != listing.listing_agent_id
        ):
            await self._assert_can_assign_to(updates["listing_agent_id"])
        if updates.get("client_id") is not None:
            await self._assert_client_exists(updates["client_id"])

        # Coordinates must stay a pair through a PATCH as well as a POST —
        # PropertyUpdate cannot check it alone, because clearing one and
        # leaving the other is only invalid against the merged record.
        merged_lat = updates.get("latitude", listing.latitude)
        merged_lon = updates.get("longitude", listing.longitude)
        if (merged_lat is None) != (merged_lon is None):
            raise ConflictError(
                "latitude and longitude must be set together, or both cleared."
            )

        snapshot_before = take_snapshot(ENTITY_TYPE, listing)
        before = {field: getattr(listing, field) for field in AUDITED_FIELDS}
        for field, value in updates.items():
            setattr(listing, field, value)
        listing.updated_by = actor.id
        await self._flush_checking_mls()

        after = {field: getattr(listing, field) for field in AUDITED_FIELDS}
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
                entity_id=listing.id,
                metadata={"changes": diff},
            )
            await record_event(
                self.session,
                organization_id=self.auth.organization_id,
                event_type="property.updated",
                entity_type="property",
                record=listing,
                actor_id=actor.id,
                previous=snapshot_before,
                by_automation=is_automation_actor(self.auth.role_keys),
            )
            logger.info(
                "property_updated",
                extra={"property_id": str(listing.id), "fields": sorted(diff)},
            )
        return await self._reload(listing)

    async def delete_property(self, property_id: UUID, actor: User) -> None:
        """Soft delete. The row and its audit trail survive."""
        listing = await self._load_for_write(property_id)

        listing.deleted_at = datetime.now(UTC)
        listing.updated_by = actor.id
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_DELETED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=listing.id,
            metadata={"address": listing.full_address, "status": listing.status},
        )
        logger.info("property_deleted", extra={"property_id": str(listing.id)})

    async def assign_property(
        self, property_id: UUID, agent_id: UUID, actor: User
    ) -> Property:
        """Reassign the listing agent. Gated on `properties.assign`."""
        self.auth.require("properties.assign")
        await self._assert_can_assign_to(agent_id)

        listing = await self._load_for_write(property_id)

        previous = listing.listing_agent_id
        listing.listing_agent_id = agent_id
        listing.updated_by = actor.id
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_UPDATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=listing.id,
            metadata={
                "changes": {
                    "listing_agent_id": {
                        "old": str(previous) if previous else None,
                        "new": str(agent_id),
                    }
                }
            },
        )
        return await self._reload(listing)

    # ------------------------------------------------------------ helpers

    async def _flush_checking_mls(self) -> None:
        """Turn the MLS uniqueness violation into a 409 rather than a 500."""
        try:
            await self.session.flush()
        except IntegrityError as exc:
            if "uq_properties_org_mls" in str(exc.orig):
                raise ConflictError(
                    "A listing with this MLS number already exists."
                ) from exc
            raise

    async def _reload(self, listing: Property) -> Property:
        """Re-read server-generated columns after a write.

        `updated_at` has a server-side onupdate and `search_vector` is
        generated, so SQLAlchemy expires both after an UPDATE. Reading either
        while the router serialises the response would trigger a lazy load
        outside the async context — MissingGreenlet, a 500 on every PATCH.
        """
        await self.session.refresh(listing, ["updated_at"])
        return listing

    async def _assert_client_exists(self, client_id: UUID) -> None:
        """The seller must be a client of this workspace.

        Checked against the tenant, not the caller's client scope: an agent may
        legitimately list a property for a colleague's client, and requiring
        `contacts.view` here would couple the two permissions together.
        """
        client = await self.clients.get(client_id, self.auth.organization_id)
        if client is None:
            raise NotFoundError("Client not found in this workspace.")

    async def _assert_can_assign_to(self, agent_id: UUID) -> None:
        """The assignee must be a real member of this workspace.

        Without this check an id from another tenant could be written into
        `listing_agent_id`. RLS would then hide nothing — properties are
        visible org-wide — but the listing would show an agent who cannot be
        resolved, and OWN-scope writes against it would be impossible.
        """
        target = await self.users.get(agent_id, self.auth.organization_id)
        if target is None:
            raise NotFoundError("Assignee is not a member of this workspace.")

        scope = self.auth.scope_for("properties.assign")
        if scope is None:
            raise PermissionDeniedError(
                "This action requires the 'properties.assign' permission."
            )
        if scope is Scope.OWN and agent_id != self.auth.user_id:
            raise PermissionDeniedError("You can only assign listings to yourself.")
        if scope is Scope.TEAM:
            teammates = await self.rbac.team_member_ids(
                self.auth.user_id, self.auth.organization_id
            )
            if agent_id not in teammates:
                raise PermissionDeniedError(
                    "You can only assign listings within your team."
                )
