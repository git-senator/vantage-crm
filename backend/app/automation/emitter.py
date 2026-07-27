"""The one call CRM services make to feed the automation engine.

Services call `record_event` at the same points they call `AuditService.record`.
Keeping it to a single helper matters: eight services each writing their own
outbox row and their own enqueue is eight places for the recursion marker or the
snapshot field list to be forgotten, and the symptom of forgetting either is a
workflow loop that sends real email.

**Snapshot fields are declared here, per entity, explicitly.** Reflecting over
the model would pick up whatever gets added to the table later — including
columns nobody meant to expose to a workflow author, and `password_hash` lives
on one of these models. The list is also the vocabulary the builder offers for
conditions, so it is a contract rather than an implementation detail.

**Emission never fails the caller.** The outbox row is part of the caller's
transaction and cannot be lost; the *enqueue* is best-effort, exactly like every
other enqueue in this codebase, because the sweep re-finds anything it drops.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.events import emit, snapshot
from app.core.logging import get_logger
from app.webhooks.events import WEBHOOK_EVENT_TYPES
from app.workers.queue import JobName, enqueue

logger = get_logger(__name__)

#: What a workflow can see and test, per entity.
#:
#: Deliberately excludes free-text bodies (`notes`, `description`) beyond what a
#: condition would plausibly compare — the payload is stored on every event and
#: copied into every run's context, and a lead's notes field would dominate both.
SNAPSHOT_FIELDS: dict[str, tuple[str, ...]] = {
    "lead": (
        "id", "first_name", "last_name", "full_name", "email", "phone",
        "stage", "status", "source", "temperature", "budget_min", "budget_max",
        "currency", "preferred_location", "tags", "score", "owner_id",
        "last_contacted_at", "converted_client_id", "created_at",
    ),
    "client": (
        "id", "first_name", "last_name", "company_name", "display_name",
        "is_company", "email", "phone", "type", "status", "lifetime_value",
        "currency", "client_since", "tags", "owner_id", "source_lead_id",
        "created_at",
    ),
    "property": (
        "id", "title", "mls_number", "status", "property_type", "city",
        "state", "postal_code", "price", "currency", "bedrooms", "bathrooms",
        "square_feet", "year_built", "listed_at", "client_id",
        "listing_agent_id", "created_at",
    ),
    "deal": (
        "id", "title", "status", "value", "currency", "probability",
        "expected_close_date", "closed_at", "lost_reason", "pipeline_id",
        "stage_id", "client_id", "property_id", "owner_id", "created_at",
    ),
    "task": (
        "id", "title", "status", "priority", "due_at", "completed_at",
        "assignee_id", "entity_type", "entity_id", "created_at",
    ),
    "note": (
        "id", "title", "entity_type", "entity_id", "author_id", "is_pinned",
        "created_at",
    ),
    "activity": (
        "id", "type", "subject", "entity_type", "entity_id", "actor_id",
        "occurred_at", "created_at",
    ),
    "conversation": (
        "id", "channel", "external_id", "display_name", "subject",
        "entity_type", "entity_id", "owner_id", "unread_count", "created_at",
    ),
    "calendar_event": (
        "id", "title", "event_type", "status", "starts_at", "ends_at",
        "location", "owner_id", "entity_type", "entity_id", "created_at",
    ),
}


def take_snapshot(entity_type: str, record: Any) -> dict[str, Any]:
    """A JSON-safe projection of a record, using this entity's field list."""
    return snapshot(record, SNAPSHOT_FIELDS.get(entity_type, ("id",)))


async def record_event(
    session: AsyncSession,
    *,
    organization_id: UUID,
    event_type: str,
    entity_type: str,
    record: Any,
    actor_id: UUID | None = None,
    previous: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    by_automation: bool = False,
) -> None:
    """Write an outbox event and try to dispatch it.

    `by_automation` marks a change a workflow itself caused, so the dispatcher
    refuses to start another run from it. Callers pass
    `by_automation=context.is_automation` — see `AuthorizationContext.role_keys`,
    which carries the `system` marker for machine work.
    """
    event = await emit(
        session,
        organization_id=organization_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=getattr(record, "id", None),
        actor_id=actor_id,
        record=take_snapshot(entity_type, record),
        previous=previous,
        extra=extra,
        by_automation=by_automation,
    )

    # Best-effort. The row is committed with the change, so a lost enqueue is a
    # minute of latency until the sweep finds it, not a missed automation.
    await enqueue(
        JobName.DISPATCH_WORKFLOW_EVENT,
        str(event.id),
        str(organization_id),
        job_id=f"wfevent:{event.id}",
    )

    # The same outbox row feeds outbound webhooks (Phase 7.3). Only events a
    # webhook can subscribe to are dispatched, so the common case adds no queue
    # traffic; the delivery sweep is the net for a lost enqueue.
    if event_type in WEBHOOK_EVENT_TYPES:
        await enqueue(
            JobName.DISPATCH_WEBHOOK_EVENT,
            str(event.id),
            str(organization_id),
            job_id=f"whevent:{event.id}",
        )


def is_automation_actor(role_keys: tuple[str, ...]) -> bool:
    """Whether this authorization context belongs to a running workflow.

    The `system` role key is set by `system_context`, which only machine work
    uses. Checking the context rather than threading a flag through every
    service call means a service cannot forget to pass it.
    """
    return "system" in role_keys
