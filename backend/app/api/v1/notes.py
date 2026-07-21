"""Note endpoints.

Two read shapes, mirroring activities:

  * `GET /notes?entity_type=&entity_id=` — one record's notes, pinned first.
    Gated on being able to read that record, not on a note permission.
  * `GET /notes` — the cross-entity feed, gated on `notes.view`.

Writes need `notes.manage` and readability of the parent; editing and deleting
additionally need authorship (or `notes.manage` at ALL scope).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.v1.dependencies import (
    Authorization,
    CurrentUser,
    TenantSessionDep,
    require,
    verify_csrf,
)
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Cursor, Page, PageMeta
from app.schemas.note import (
    NoteCreate,
    NoteFilters,
    NoteRead,
    NoteUpdate,
)
from app.services.note import NoteService

router = APIRouter()


def _to_read(note, viewer_id: UUID | None) -> NoteRead:  # type: ignore[no-untyped-def]
    return NoteRead(
        id=note.id,
        entity_type=note.entity_type,
        entity_id=note.entity_id,
        title=note.title,
        body=note.body,
        content_format=note.content_format,
        is_pinned=note.is_pinned,
        author=(
            {
                "id": note.author.id,
                "full_name": note.author.full_name,
                "initials": note.author.initials,
                "avatar_hue": note.author.avatar_hue,
            }
            if note.author
            else None
        ),
        is_own=note.author_id == viewer_id,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


@router.get("", response_model=Page[NoteRead])
async def list_notes(
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    author_id: UUID | None = None,
    pinned: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> Page[NoteRead]:
    """A record's notes when an entity is named, otherwise the caller's feed."""
    filters = NoteFilters.model_validate(
        {
            "search": search,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "author_id": author_id,
            "pinned": pinned,
        }
    )
    service = NoteService(session, auth)

    if entity_type is not None and entity_id is not None:
        rows = await service.list_for_entity(
            entity_type=entity_type, entity_id=entity_id, limit=limit
        )
        return Page[NoteRead](
            data=[_to_read(row, user.id) for row in rows],
            meta=PageMeta(next_cursor=None, has_more=False, limit=limit),
        )

    decoded = Cursor.decode(cursor) if cursor else None
    rows, has_more = await service.list_feed(
        filters=filters, limit=limit, cursor=decoded
    )
    next_cursor = (
        Cursor(created_at=rows[-1].created_at, id=rows[-1].id).encode()
        if rows and has_more
        else None
    )
    return Page[NoteRead](
        data=[_to_read(row, user.id) for row in rows],
        meta=PageMeta(next_cursor=next_cursor, has_more=has_more, limit=limit),
    )


@router.get("/{note_id}", response_model=NoteRead)
async def get_note(
    note_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> NoteRead:
    return _to_read(await NoteService(session, auth).get_note(note_id), user.id)


@router.post(
    "",
    response_model=NoteRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_csrf)],
)
async def create_note(
    payload: NoteCreate,
    response: Response,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("notes.manage"))],
    user: CurrentUser,
) -> NoteRead:
    note = await NoteService(session, auth).create_note(payload, user)
    response.headers["Location"] = f"/api/v1/notes/{note.id}"
    return _to_read(note, user.id)


@router.patch(
    "/{note_id}",
    response_model=NoteRead,
    dependencies=[Depends(verify_csrf)],
)
async def update_note(
    note_id: UUID,
    payload: NoteUpdate,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("notes.manage"))],
    user: CurrentUser,
) -> NoteRead:
    """Correct a note you wrote. 403 on someone else's, unless ALL scope."""
    return _to_read(
        await NoteService(session, auth).update_note(note_id, payload, user), user.id
    )


@router.delete(
    "/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_csrf)],
)
async def delete_note(
    note_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("notes.manage"))],
    user: CurrentUser,
) -> None:
    """Soft delete. The audit trail survives."""
    await NoteService(session, auth).delete_note(note_id, user)
