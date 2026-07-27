"""The webhook event vocabulary and the payload envelope.

`WEBHOOK_EVENT_TYPES` is the closed set a caller may subscribe to. It is
imported by the emitter to decide whether an outbox event is worth a webhook
dispatch at all, and by the management schema to reject a subscription to an
event that will never fire. Keeping it here — not in the emitter, not in the
API — means there is one list, and adding an event is one edit.

The envelope is intentionally small and stable: a receiver builds against it,
so it exposes the event's identity, timing and the record snapshot the outbox
already carries, and nothing that would tie it to internal table shape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.models.webhook import WebhookDelivery

#: The events a webhook may subscribe to (Phase 7.3). A closed set on purpose:
#: every one corresponds to an event the CRM services already emit into the
#: outbox, so a subscription can never wait on something that is never sent.
WEBHOOK_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "lead.created",
        "lead.updated",
        "deal.created",
        "deal.updated",
        "property.created",
        "task.completed",
    }
)


def build_payload(
    *,
    delivery_id: UUID,
    event_id: UUID,
    event_type: str,
    organization_id: UUID,
    event_payload: dict[str, Any],
) -> dict[str, Any]:
    """The JSON body for one delivery.

    `event_payload` is the outbox event's stored payload — already a JSON-safe,
    redacted snapshot with `record`, `previous` and `changed_fields`. This
    reshapes it into the public envelope; it never reaches back to the live row,
    so the delivered body is the record as it was when the event fired.
    """
    envelope: dict[str, Any] = {
        # The delivery id doubles as the consumer's idempotency key.
        "id": str(delivery_id),
        "event": event_type,
        "event_id": str(event_id),
        "created_at": datetime.now(UTC).isoformat(),
        "organization_id": str(organization_id),
        "data": event_payload.get("record", {}),
    }
    if event_payload.get("previous"):
        envelope["previous"] = event_payload["previous"]
    if event_payload.get("changed_fields"):
        envelope["changed_fields"] = event_payload["changed_fields"]
    return envelope


def payload_for(delivery: WebhookDelivery) -> dict[str, Any]:
    """The stored body of an existing delivery."""
    return dict(delivery.payload or {})
