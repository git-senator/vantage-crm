"""Entity context builders: scoped CRM data → prompt content.

When a conversation is anchored to a record ("help me with this lead"), the
assistant needs that record in front of it. This is where that context is
assembled, and it is the single most security-sensitive module in the assistant
because it is the point where CRM data crosses into a prompt.

Two rules, both non-negotiable:

  * **Fetched under the caller's scope.** Every builder goes through the
    entity's own scoped service — `LeadService.get_lead`, `DealService.get_deal`
    — which raises `NotFoundError` for a record the caller cannot see. A record
    invisible in the UI is invisible to the assistant. This is the flagship
    RAG-bypasses-RBAC control (SECURITY.md §5), and it is enforced by *reuse*:
    the assistant does not query entities itself, it asks the same services a
    request handler would.
  * **Redacted and fenced.** Every value becomes an untrusted, redacted
    `ContentBlock` via the 6.1 primitives, so a lead's notes reach the model as
    delimited data it may read but never obey, with direct identifiers masked.

The registry maps `entity_type → builder`. Adding an entity is one entry;
nothing else in the assistant changes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.context import fields, instruction
from app.ai.prompts import ContentBlock
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.services.rbac import AuthorizationContext

#: A builder: given a scope and an id, produce the entity's context blocks, or
#: `None` if the caller cannot see it. `None` is not an error — it is the honest
#: answer that the anchor is unreachable for this user.
ContextBuilder = Callable[
    [AsyncSession, AuthorizationContext, UUID],
    Awaitable[list[ContentBlock] | None],
]


def _money(value: object | None) -> str | None:
    return str(value) if value is not None else None


async def _lead(
    session: AsyncSession, auth: AuthorizationContext, entity_id: UUID
) -> list[ContentBlock] | None:
    from app.services.lead import LeadService

    try:
        lead = await LeadService(session, auth).get_lead(entity_id)
    except (NotFoundError, PermissionDeniedError):
        return None
    return [
        instruction("The user is asking about this lead:"),
        *fields(
            [
                ("name", f"{lead.first_name} {lead.last_name}".strip()),
                ("stage", lead.stage),
                ("status", lead.status),
                ("source", lead.source),
                ("temperature", lead.temperature),
                ("budget_min", _money(lead.budget_min)),
                ("budget_max", _money(lead.budget_max)),
                ("preferred_location", lead.preferred_location),
                ("score", str(lead.score) if lead.score is not None else None),
                ("tags", ", ".join(lead.tags) if lead.tags else None),
                ("notes", lead.notes),
            ]
        ),
    ]


async def _client(
    session: AsyncSession, auth: AuthorizationContext, entity_id: UUID
) -> list[ContentBlock] | None:
    from app.services.client import ClientService

    try:
        client = await ClientService(session, auth).get_client(entity_id)
    except (NotFoundError, PermissionDeniedError):
        return None
    name = f"{client.first_name or ''} {client.last_name or ''}".strip()
    return [
        instruction("The user is asking about this client:"),
        *fields(
            [
                ("name", name or client.company_name),
                ("company", client.company_name),
                ("type", client.type),
                ("status", client.status),
                ("lifetime_value", _money(client.lifetime_value)),
                ("tags", ", ".join(client.tags) if client.tags else None),
                ("notes", client.notes),
            ]
        ),
    ]


async def _property(
    session: AsyncSession, auth: AuthorizationContext, entity_id: UUID
) -> list[ContentBlock] | None:
    from app.services.property import PropertyService

    try:
        prop = await PropertyService(session, auth).get_property(entity_id)
    except (NotFoundError, PermissionDeniedError):
        return None
    return [
        instruction("The user is asking about this property listing:"),
        *fields(
            [
                ("title", prop.title),
                ("status", prop.status),
                ("type", prop.property_type),
                ("city", prop.city),
                ("state", prop.state),
                ("price", _money(prop.price)),
                ("bedrooms", str(prop.bedrooms) if prop.bedrooms is not None else None),
                (
                    "bathrooms",
                    str(prop.bathrooms) if prop.bathrooms is not None else None,
                ),
                ("features", ", ".join(prop.features) if prop.features else None),
                ("description", prop.description),
            ]
        ),
    ]


async def _deal(
    session: AsyncSession, auth: AuthorizationContext, entity_id: UUID
) -> list[ContentBlock] | None:
    from app.services.deal import DealService

    try:
        deal = await DealService(session, auth).get_deal(entity_id)
    except (NotFoundError, PermissionDeniedError):
        return None
    # `stage` is eager-loaded on the deal, so this is the board's own stage name
    # rather than a second lookup that could disagree with it.
    stage_name = deal.stage.name if deal.stage is not None else None
    return [
        instruction("The user is asking about this deal:"),
        *fields(
            [
                ("title", deal.title),
                ("stage", stage_name),
                ("value", _money(deal.value)),
                (
                    "probability",
                    str(deal.probability) if deal.probability is not None else None,
                ),
                ("priority", deal.priority),
                (
                    "expected_close_date",
                    deal.expected_close_date.isoformat()
                    if deal.expected_close_date
                    else None,
                ),
                ("lost_reason", deal.lost_reason),
            ]
        ),
    ]


async def _task(
    session: AsyncSession, auth: AuthorizationContext, entity_id: UUID
) -> list[ContentBlock] | None:
    from app.services.task import TaskService

    try:
        task = await TaskService(session, auth).get_task(entity_id)
    except (NotFoundError, PermissionDeniedError):
        return None
    return [
        instruction("The user is asking about this task:"),
        *fields(
            [
                ("title", task.title),
                ("status", task.status),
                ("priority", task.priority),
                ("due_at", task.due_at.isoformat() if task.due_at else None),
                ("description", task.description),
            ]
        ),
    ]


async def _note(
    session: AsyncSession, auth: AuthorizationContext, entity_id: UUID
) -> list[ContentBlock] | None:
    from app.services.note import NoteService

    try:
        note = await NoteService(session, auth).get_note(entity_id)
    except (NotFoundError, PermissionDeniedError):
        return None
    return [
        instruction("The user is asking about this note:"),
        *fields([("about", note.entity_type), ("body", note.body)]),
    ]


#: entity_type → builder. The vocabulary matches the model's CHECK constraint and
#: the notes/activities polymorphic shape. Adding an entity is one line here plus
#: a builder above.
ENTITY_CONTEXT_BUILDERS: dict[str, ContextBuilder] = {
    "lead": _lead,
    "client": _client,
    "property": _property,
    "deal": _deal,
    "task": _task,
    "note": _note,
}


async def build_entity_context(
    session: AsyncSession,
    auth: AuthorizationContext,
    entity_type: str,
    entity_id: UUID,
) -> list[ContentBlock] | None:
    """The context blocks for one anchored entity, or `None` if unreachable.

    `None` when the type is unknown or the caller cannot see the record — the
    caller treats both the same way, by carrying on without entity context
    rather than leaking that the record exists.
    """
    builder = ENTITY_CONTEXT_BUILDERS.get(entity_type)
    if builder is None:
        return None
    return await builder(session, auth, entity_id)


__all__ = [
    "ENTITY_CONTEXT_BUILDERS",
    "ContextBuilder",
    "build_entity_context",
]
