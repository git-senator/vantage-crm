"""Domain events — the transactional outbox that starts workflows.

CRM services call `emit` at the same moments they call `AuditService.record`.
The two are deliberately separate despite firing together: the audit log answers
*what happened, for the record*, and is valuable precisely because it is
append-only and never re-processed. This answers *what should react*, and needs
retry, replay and a dispatched flag. Putting both jobs on one table would drag
the second set of requirements onto the first.

**Why an outbox rather than the best-effort enqueue used everywhere else.**
Every other enqueue in this codebase may be lost, because the worst case is a
missing notification or a delayed sweep. Here the worst case is a *phantom
trigger*: a workflow reacting to a change that rolled back, sending a real
customer a real email about something that never happened. The event row commits
in the caller's transaction, so it exists exactly when the change does. A
dispatcher is then enqueued optimistically, and a sweep re-finds anything that
enqueue lost — the same belt-and-braces `sweep_scan_backlog` uses.

**The recursion guard.** A workflow's own writes emit events, so "when a lead is
updated, update the lead" is a loop that costs one row per iteration. Events
carry the automation actor marker; the dispatcher refuses to start a run from an
event a workflow caused. That is stricter than tracking depth — it means a
workflow can never trigger another workflow — and it is the right default for a
feature whose failure mode is unbounded outbound email. Chaining, when somebody
needs it, is an explicit follow-up with its own budget.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger, redact
from app.models.automation import WorkflowEvent
from app.services.audit import json_safe

logger = get_logger(__name__)

#: Marker written into a payload by anything a workflow did. The dispatcher
#: refuses these, which is what stops a workflow from triggering itself.
AUTOMATION_SOURCE = "automation"

#: Cap on a serialised payload. A record with a huge notes field would
#: otherwise make every event row carry it, and conditions only ever look at
#: scalar fields.
MAX_FIELD_LENGTH = 2_000


def snapshot(record: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    """A JSON-safe projection of a record, for conditions to read.

    An explicit field list rather than reflection over the model: a snapshot
    built from `__dict__` picks up whatever gets added to the table later,
    including columns nobody meant to expose to a workflow author — and
    `password_hash` lives on one of these models.
    """
    projection: dict[str, Any] = {}
    for name in fields:
        value = json_safe(getattr(record, name, None))
        if isinstance(value, str) and len(value) > MAX_FIELD_LENGTH:
            value = value[:MAX_FIELD_LENGTH]
        projection[name] = value
    return projection


async def emit(
    session: AsyncSession,
    *,
    organization_id: UUID,
    event_type: str,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    actor_id: UUID | None = None,
    record: dict[str, Any] | None = None,
    previous: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    by_automation: bool = False,
) -> WorkflowEvent:
    """Write one event into the caller's transaction.

    Returns the row rather than nothing so the caller can enqueue a dispatch
    for its id — the fast path. Losing that enqueue costs latency, not the
    event, because the row is already committed and the sweep will find it.

    `previous` is the before-image on an update. `changed_fields` is derived
    here rather than by the dispatcher so the comparison happens against the
    values as they were, not against whatever the record has become by the time
    a worker picks the event up.
    """
    payload: dict[str, Any] = {
        "record": record or {},
        "previous": previous or {},
        **(extra or {}),
    }

    if previous and record:
        payload["changed_fields"] = sorted(
            name
            for name, before in previous.items()
            if name in record and record[name] != before
        )
    else:
        payload["changed_fields"] = []

    if by_automation:
        payload["source"] = AUTOMATION_SOURCE

    event = WorkflowEvent(
        organization_id=organization_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_id=actor_id,
        payload=json_safe(redact(payload)),
    )
    session.add(event)
    await session.flush()

    logger.info(
        "workflow_event_emitted",
        extra={
            "event_id": str(event.id),
            "event_type": event_type,
            "by_automation": by_automation,
        },
    )
    return event


def was_caused_by_automation(payload: dict[str, Any]) -> bool:
    """Whether this event is a workflow's own doing."""
    return payload.get("source") == AUTOMATION_SOURCE
