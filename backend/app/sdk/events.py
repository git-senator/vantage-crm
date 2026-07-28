"""Typed event contracts — the shape of what a plugin receives.

Each event a plugin can subscribe to has a contract: an entity, a description,
and a typed payload model. A plugin parses an incoming delivery with
``parse_event`` and gets a validated envelope whose ``data`` conforms to the
entity's payload — so a handler works against typed fields, not a raw dict, and a
payload the platform never sends is rejected at the boundary.

The payload models are permissive (``extra="allow"``): the platform may add
fields within a minor version, and a plugin built against the older contract must
keep working. The known fields are the stable part of the contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.sdk.errors import SdkValidationError


class _Payload(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str | None = None


class LeadPayload(_Payload):
    email: str | None = None
    full_name: str | None = None
    stage: str | None = None
    status: str | None = None
    owner_id: str | None = None


class DealPayload(_Payload):
    title: str | None = None
    value: float | None = None
    currency: str | None = None
    status: str | None = None
    stage_id: str | None = None
    owner_id: str | None = None


class PropertyPayload(_Payload):
    title: str | None = None
    status: str | None = None
    price: float | None = None
    city: str | None = None


class TaskPayload(_Payload):
    title: str | None = None
    status: str | None = None
    assignee_id: str | None = None


@dataclass(frozen=True, slots=True)
class EventContract:
    event_type: str
    entity: str
    description: str
    payload_model: type[_Payload]


EVENT_CONTRACTS: dict[str, EventContract] = {
    c.event_type: c
    for c in (
        EventContract("lead.created", "lead", "A lead was created.", LeadPayload),
        EventContract("lead.updated", "lead", "A lead was updated.", LeadPayload),
        EventContract("deal.created", "deal", "A deal was created.", DealPayload),
        EventContract("deal.updated", "deal", "A deal was updated.", DealPayload),
        EventContract("property.created", "property", "A property was listed.",
                      PropertyPayload),
        EventContract("task.completed", "task", "A task was completed.", TaskPayload),
    )
}


def event_contract(event_type: str) -> EventContract:
    try:
        return EVENT_CONTRACTS[event_type]
    except KeyError as exc:
        raise KeyError(f"Unknown event type '{event_type}'.") from exc


def is_known_event(event_type: str) -> bool:
    return event_type in EVENT_CONTRACTS


class PluginEvent(BaseModel):
    """The envelope a plugin handler receives — the public shape of a delivery."""

    id: str
    event: str
    event_id: str
    created_at: str
    organization_id: str
    data: dict[str, Any]


def parse_event(payload: Mapping[str, Any]) -> PluginEvent:
    """Validate a raw delivery into a typed :class:`PluginEvent`.

    The ``data`` block is validated against the event's payload model, so a
    malformed payload (or an unknown event) is an :class:`SdkValidationError`
    rather than a handler crash three lines later.
    """
    event_type = payload.get("event")
    if not isinstance(event_type, str) or event_type not in EVENT_CONTRACTS:
        raise SdkValidationError(f"Unknown or missing event '{event_type}'.")
    contract = EVENT_CONTRACTS[event_type]
    try:
        data = contract.payload_model.model_validate(payload.get("data") or {})
        return PluginEvent(
            id=str(payload.get("id", "")),
            event=event_type,
            event_id=str(payload.get("event_id", "")),
            created_at=str(payload.get("created_at", "")),
            organization_id=str(payload.get("organization_id", "")),
            data=data.model_dump(),
        )
    except ValueError as exc:
        raise SdkValidationError(f"Invalid payload for '{event_type}': {exc}") from exc


__all__ = [
    "EVENT_CONTRACTS",
    "DealPayload",
    "EventContract",
    "LeadPayload",
    "PluginEvent",
    "PropertyPayload",
    "TaskPayload",
    "event_contract",
    "is_known_event",
    "parse_event",
]
