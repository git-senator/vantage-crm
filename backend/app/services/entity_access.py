"""Can this caller read this record?

Activities, tasks and notes all hang off a polymorphic (`entity_type`,
`entity_id`) pair, and all three need the same answer to the same question
before they will show or attach anything.

**Why this exists rather than an owner column on each of them.** An activity's
visibility follows the record it describes, not who typed it: an agent must see
every activity on their own lead, whoever logged it, and must see none on a
colleague's. Scoping the child by its own `actor_id` would put holes in the
owner's timeline and leak nothing useful. So the child tables carry no scope
anchor at all, and every read is gated on the parent instead.

That makes this module a security boundary. Each entity keeps its own rules —
properties are shared inventory at ALL scope, leads and deals are personal —
and this delegates to the same repository predicates the entity's own endpoints
use, so there is exactly one definition of "visible" per entity.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.repositories.client import ClientRepository
from app.repositories.deal import DealRepository
from app.repositories.lead import LeadRepository
from app.repositories.note import NoteRepository
from app.repositories.property import PropertyRepository
from app.repositories.task import TaskRepository
from app.services.rbac import AuthorizationContext, RbacService

#: entity_type -> the permission that governs reading it.
VIEW_PERMISSIONS: dict[str, str] = {
    "lead": "leads.view",
    "client": "contacts.view",
    "property": "properties.view",
    "deal": "deals.view",
    "task": "tasks.view",
    "note": "notes.view",
}

#: Entity types that carry no scope anchor of their own and delegate upward.
#: A note's readability is its parent record's readability — which is the same
#: rule the note endpoints already apply, expressed once here so an attachment
#: on a note cannot become a way around it.
DELEGATING_TYPES: frozenset[str] = frozenset({"note"})


class EntityAccess:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.rbac = RbacService(session)

    async def assert_readable(self, entity_type: str, entity_id: UUID) -> None:
        """Raise unless the caller can read this record.

        404 rather than 403 throughout, including for an unknown entity_type:
        the child endpoints must not become a way to probe which records exist.
        Properties are the documented exception elsewhere, but that exception
        exists because a listing is already in the caller's own list — it does
        not extend to guessing at ids through an activity endpoint.
        """
        permission = VIEW_PERMISSIONS.get(entity_type)
        if permission is None:
            raise NotFoundError("Unknown entity type.")

        scope = self.auth.scope_for(permission)
        if scope is None:
            # No grant on the parent resource at all.
            raise NotFoundError("Record not found.")

        organization_id = self.auth.organization_id

        if entity_type in DELEGATING_TYPES:
            await self._assert_delegated(entity_type, entity_id, organization_id)
            return

        scoped_ids = await self.rbac.owner_ids_for_scope(self.auth, scope)

        found: object | None
        match entity_type:
            case "lead":
                found = await LeadRepository(self.session).get_visible(
                    entity_id, organization_id, scoped_ids
                )
            case "client":
                found = await ClientRepository(self.session).get_visible(
                    entity_id, organization_id, scoped_ids
                )
            case "property":
                found = await PropertyRepository(self.session).get_visible(
                    entity_id, organization_id, scoped_ids
                )
            case "deal":
                found = await DealRepository(self.session).get_visible(
                    entity_id, organization_id, scoped_ids
                )
            case "task":
                # A task's scope anchor is its assignee, not an owner — but the
                # scope resolver returns the same id set either way, and the
                # repository applies it against the right column.
                found = await TaskRepository(self.session).get_visible(
                    entity_id, organization_id, scoped_ids
                )
            case _:  # pragma: no cover - guarded above
                found = None

        if found is None:
            raise NotFoundError("Record not found.")

    async def _assert_delegated(
        self, entity_type: str, entity_id: UUID, organization_id: UUID
    ) -> None:
        """Resolve a scope-less child by asking about its parent instead.

        Exactly one hop, and that is guaranteed rather than assumed: a note's
        own `entity_type` CHECK excludes `note`, so the recursion cannot
        continue past this call. If a future delegating type could nest, this
        needs a depth guard — the constraint is what makes one unnecessary now.
        """
        if entity_type != "note":  # pragma: no cover — guarded by the caller
            raise NotFoundError("Record not found.")

        note = await NoteRepository(self.session).get(entity_id, organization_id)
        if note is None:
            raise NotFoundError("Record not found.")
        await self.assert_readable(note.entity_type, note.entity_id)

    async def readable_or_none(self, entity_type: str, entity_id: UUID) -> bool:
        """Boolean form, for filtering a mixed list without raising."""
        try:
            await self.assert_readable(entity_type, entity_id)
        except NotFoundError:
            return False
        return True
