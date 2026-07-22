"""Unified timeline contracts.

The timeline merges two sources into one chronology — **activities** and
**notes** — and nothing else. Task events (created, completed, reopened,
assigned) already reach it as the activities those actions write; they are not a
third source, or they would be counted twice. The audit log is deliberately
absent: it is the security record, not the business timeline.

A `TimelineItem` is the shared shape both sources render into. `kind`
discriminates; `timestamp` is what the list orders by — `occurred_at` for an
activity (when it happened, which is not always when it was logged),
`created_at` for a note.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

TimelineKind = Literal["activity", "note"]


class TimelineActor(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    full_name: str
    initials: str
    avatar_hue: int


class TimelineItem(BaseModel):
    kind: TimelineKind
    id: UUID
    entity_type: str
    entity_id: UUID
    #: The chronological key. `occurred_at` for activities, `created_at` for
    #: notes — the whole point of merging on one axis.
    timestamp: datetime
    #: activity.type ("call", "stage_change", …) or "note".
    type: str
    title: str | None
    body: str | None
    actor: TimelineActor | None
    #: Activities only: system-written entries (stage_change) the UI locks.
    is_system: bool = False
    #: Notes only: pinned notes the UI may surface even out of order.
    is_pinned: bool = False
    metadata: dict[str, Any] = {}
