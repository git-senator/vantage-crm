"""Activity contracts.

`type` is constrained to a known vocabulary rather than free text because the
timeline renders an icon per type and analytics group by it. `stage_change` is
in the vocabulary but is **system-written only** — accepting it from a client
would let anyone forge a funnel event, so `ActivityCreate` narrows to the
manual types.

`occurred_at` is separate from `created_at` on purpose: a call logged on Monday
may have happened on Friday, and a timeline ordered by when someone got round
to typing it is not a timeline.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

ActivityType = Literal["call", "email", "meeting", "note", "showing", "stage_change"]
#: What a person may log. `stage_change` is written by the deal transition and
#: is deliberately not in this set.
ManualActivityType = Literal["call", "email", "meeting", "note", "showing"]

EntityType = Literal["lead", "client", "property", "deal"]


class ActivityActor(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    full_name: str
    initials: str
    avatar_hue: int


class ActivityCreate(BaseModel):
    entity_type: EntityType
    entity_id: UUID
    type: ManualActivityType = "note"
    subject: str = Field(min_length=1, max_length=300)
    body: str | None = Field(default=None, max_length=20_000)
    #: Defaults to now when omitted. May be backdated; may not be invented for
    #: the future — see the validator.
    occurred_at: datetime | None = None

    @model_validator(mode="after")
    def _not_in_the_future(self) -> ActivityCreate:
        """A logged activity records something that happened.

        Scheduling is what tasks are for. Allowing a future `occurred_at` here
        would put entries at the top of a timeline for things nobody has done,
        which is the fastest way to make a timeline untrustworthy.
        """
        if self.occurred_at is not None:
            from datetime import UTC, timedelta

            # A minute of slack absorbs clock skew between client and server.
            if self.occurred_at > datetime.now(UTC) + timedelta(minutes=1):
                raise ValueError(
                    "occurred_at cannot be in the future. Create a task instead."
                )
        return self


class ActivityUpdate(BaseModel):
    """Only what a person can legitimately correct after the fact.

    `entity_type`/`entity_id` are absent: moving an activity to another record
    rewrites two timelines at once, and is better expressed as delete-and-log.
    `type` is absent because changing a call into a showing changes what the
    entry means.
    """

    subject: str | None = Field(default=None, min_length=1, max_length=300)
    body: str | None = Field(default=None, max_length=20_000)
    occurred_at: datetime | None = None


class ActivityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entity_type: str
    entity_id: UUID
    type: str
    subject: str
    body: str | None
    occurred_at: datetime
    metadata: dict[str, Any]
    actor: ActivityActor | None
    #: True for entries the system wrote. The UI hides edit controls on these,
    #: and the service refuses to modify them.
    is_system: bool
    created_at: datetime


class ActivityFilters(BaseModel):
    """Explicit filter parameters. Deliberately not a generic query DSL."""

    search: str | None = Field(default=None, max_length=200)
    type: ActivityType | None = None
    entity_type: EntityType | None = None
    entity_id: UUID | None = None
    actor_id: UUID | None = None
    occurred_before: datetime | None = None
    occurred_after: datetime | None = None

    @model_validator(mode="after")
    def _entity_id_needs_a_type(self) -> ActivityFilters:
        """`entity_id` alone would scan every entity's timeline for a UUID
        collision that cannot happen — and would miss the composite index."""
        if self.entity_id is not None and self.entity_type is None:
            raise ValueError("entity_type is required when filtering by entity_id")
        return self
