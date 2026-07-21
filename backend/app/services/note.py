"""Note business logic.

Authorization is the same shape as `ActivityService`, and for the same reason:
a note has **no scope anchor of its own**. Its visibility follows the record it
annotates, resolved through `EntityAccess`.

  * reading a record's notes proves the caller can read the parent first;
  * the cross-entity feed (`list_feed`) is author-scoped by `notes.view`;
  * writing needs `notes.manage` *and* readability of the parent — you cannot
    annotate a record you cannot see;
  * editing and deleting need authorship, or `notes.manage` at ALL scope. A
    colleague rewriting your note is not a correction.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.core.logging import get_logger
from app.core.permissions import Scope
from app.models.note import Note
from app.models.user import User
from app.repositories.note import NoteRepository
from app.schemas.common import Cursor
from app.schemas.note import NoteCreate, NoteFilters, NoteUpdate
from app.services.audit import AuditService, build_diff
from app.services.entity_access import EntityAccess
from app.services.rbac import AuthorizationContext, RbacService

logger = get_logger(__name__)

ENTITY_TYPE = "note"

AUDITED_FIELDS = ("title", "body", "content_format", "is_pinned")


class NoteService:
    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext | None = None
    ) -> None:
        self.session = session
        self.auth = auth
        self.notes = NoteRepository(session)
        self.audit = AuditService(session)
        self.rbac = RbacService(session)
        self.access = EntityAccess(session, auth) if auth is not None else None

    def _require_auth(self) -> AuthorizationContext:
        if self.auth is None:  # pragma: no cover - programming error
            raise RuntimeError(
                "NoteService needs an AuthorizationContext for this operation."
            )
        return self.auth

    # ------------------------------------------------------------- reading

    async def list_for_entity(
        self, *, entity_type: str, entity_id: UUID, limit: int = 50
    ) -> list[Note]:
        """One record's notes, pinned first. Gated on reading the parent."""
        auth = self._require_auth()
        assert self.access is not None
        await self.access.assert_readable(entity_type, entity_id)
        return await self.notes.list_for_entity(
            auth.organization_id,
            entity_type=entity_type,
            entity_id=entity_id,
            limit=limit,
        )

    async def list_for_entity_unchecked(
        self, *, organization_id: UUID, entity_type: str, entity_id: UUID, limit: int = 50
    ) -> list[Note]:
        """Notes without the parent check, for callers that already resolved the
        entity under RBAC (the timeline service, the deal detail endpoint)."""
        return await self.notes.list_for_entity(
            organization_id, entity_type=entity_type, entity_id=entity_id, limit=limit
        )

    async def list_feed(
        self, *, filters: NoteFilters, limit: int, cursor: Cursor | None = None
    ) -> tuple[list[Note], bool]:
        """The cross-entity feed. `notes.view` scope decides whose notes show."""
        auth = self._require_auth()
        assert self.access is not None

        scope = auth.require("notes.view")
        author_ids = await self.rbac.owner_ids_for_scope(auth, scope)

        link = filters.entity_link()
        if link is not None:
            await self.access.assert_readable(*link)

        if (
            author_ids is not None
            and filters.author_id is not None
            and filters.author_id not in author_ids
        ):
            return [], False

        return await self.notes.list_page(
            auth.organization_id,
            filters=filters,
            limit=limit,
            cursor=cursor,
            author_ids=author_ids,
        )

    async def get_note(self, note_id: UUID) -> Note:
        auth = self._require_auth()
        assert self.access is not None

        note = await self.notes.get(note_id, auth.organization_id)
        if note is None:
            raise NotFoundError("Note not found.")
        await self.access.assert_readable(note.entity_type, note.entity_id)
        return note

    # ------------------------------------------------------------- writing

    async def create_note(self, payload: NoteCreate, author: User) -> Note:
        auth = self._require_auth()
        assert self.access is not None

        auth.require("notes.manage")
        await self.access.assert_readable(payload.entity_type, payload.entity_id)

        note = Note(
            organization_id=auth.organization_id,
            author_id=author.id,
            updated_by=author.id,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            title=payload.title,
            body=payload.body,
            content_format=payload.content_format,
            is_pinned=payload.is_pinned,
        )
        self.session.add(note)
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_CREATED,
            organization_id=auth.organization_id,
            actor_id=author.id,
            actor_email=author.email,
            entity_type=ENTITY_TYPE,
            entity_id=note.id,
            metadata={"on": f"{payload.entity_type}:{payload.entity_id}"},
        )
        logger.info("note_created", extra={"note_id": str(note.id)})
        return await self.get_note(note.id)

    async def update_note(
        self, note_id: UUID, payload: NoteUpdate, actor: User
    ) -> Note:
        auth = self._require_auth()
        note = await self.get_note(note_id)
        auth.require("notes.manage")
        self._assert_own_or_all(note, "edit")

        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            return note

        before = {field: getattr(note, field) for field in AUDITED_FIELDS}
        for field, value in updates.items():
            setattr(note, field, value)
        note.updated_by = actor.id
        await self.session.flush()

        diff = build_diff(
            before, {field: getattr(note, field) for field in AUDITED_FIELDS}
        )
        if diff:
            await self.audit.record(
                action=AuditAction.RECORD_UPDATED,
                organization_id=auth.organization_id,
                actor_id=actor.id,
                actor_email=actor.email,
                entity_type=ENTITY_TYPE,
                entity_id=note.id,
                metadata={"changes": diff},
            )
        return await self.get_note(note.id)

    async def delete_note(self, note_id: UUID, actor: User) -> None:
        """Soft delete. The audit trail survives."""
        auth = self._require_auth()
        note = await self.get_note(note_id)
        auth.require("notes.manage")
        self._assert_own_or_all(note, "delete")

        note.deleted_at = datetime.now(UTC)
        note.updated_by = actor.id
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_DELETED,
            organization_id=auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=note.id,
            metadata={"on": f"{note.entity_type}:{note.entity_id}"},
        )

    # ------------------------------------------------------------- helpers

    def _assert_own_or_all(self, note: Note, verb: str) -> None:
        auth = self._require_auth()
        scope = auth.scope_for("notes.manage")
        if scope is Scope.ALL:
            return
        if note.author_id != auth.user_id:
            raise PermissionDeniedError(
                f"You can only {verb} notes you wrote yourself."
            )
