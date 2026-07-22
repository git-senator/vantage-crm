"""What can start a workflow.

Every trigger key is also the `event_type` written to the outbox, so there is
one vocabulary rather than a mapping table between "things that happen" and
"things you can listen for". A CRM service emitting `lead.created` and a
workflow listening for `lead.created` are matched by string equality on an
indexed column.

The set is deliberately coarse. `lead.updated` covers every field edit and the
workflow narrows it with a condition, rather than the registry carrying
`lead.email_changed`, `lead.budget_changed` and forty siblings — a registry that
grows with the schema is one that is permanently out of date with it.

The exceptions are the changes that are **domain actions rather than field
edits**, and they get their own triggers for the same reason they get their own
audit actions: `deal.stage_changed`, `lead.converted`, `task.completed`. Those
are the events people actually automate on, and finding them inside a generic
`updated` diff is work every workflow author would otherwise repeat.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.automation.registry import Definition, FieldOption, FieldSpec, build_registry


@dataclass(frozen=True, slots=True)
class TriggerDefinition(Definition):
    """A trigger, plus the record type its payload will describe.

    `entity_type` is what lets the builder offer the right field list for
    conditions and the right actions downstream — a `deal.*` trigger can offer
    "change stage", a `note.*` one cannot.
    """

    entity_type: str = ""


def _trigger(
    key: str,
    label: str,
    description: str,
    entity_type: str,
    category: str,
    fields: tuple[FieldSpec, ...] = (),
) -> TriggerDefinition:
    return TriggerDefinition(
        key=key,
        label=label,
        description=description,
        entity_type=entity_type,
        category=category,
        fields=fields,
    )


#: Optional narrowing on an `updated` trigger: fire only when one of these
#: fields actually changed. Without it, "when a lead is updated, email them"
#: fires on every touch including the ones the workflow itself made.
_WATCHED_FIELDS = FieldSpec(
    key="fields",
    label="Only when these fields change",
    kind="text",
    required=False,
    help_text=(
        "Comma-separated field names. Leave blank to fire on any change."
    ),
)


TRIGGERS = build_registry(
    # ------------------------------------------------------------- leads
    _trigger("lead.created", "Lead created", "A new lead is added.", "lead", "Leads"),
    _trigger(
        "lead.updated",
        "Lead updated",
        "Any field on a lead changes.",
        "lead",
        "Leads",
        (_WATCHED_FIELDS,),
    ),
    _trigger(
        "lead.converted",
        "Lead converted",
        "A lead becomes a client. A domain action, not a field edit — which is "
        "why it is its own trigger.",
        "lead",
        "Leads",
    ),
    # ----------------------------------------------------------- clients
    _trigger("client.created", "Client created", "A new client is added.", "client", "Clients"),
    _trigger(
        "client.updated",
        "Client updated",
        "Any field on a client changes.",
        "client",
        "Clients",
        (_WATCHED_FIELDS,),
    ),
    # -------------------------------------------------------- properties
    _trigger(
        "property.created",
        "Listing created",
        "A new listing is added.",
        "property",
        "Properties",
    ),
    _trigger(
        "property.updated",
        "Listing updated",
        "Any field on a listing changes.",
        "property",
        "Properties",
        (_WATCHED_FIELDS,),
    ),
    # ------------------------------------------------------------- deals
    _trigger("deal.created", "Deal created", "A new deal is opened.", "deal", "Deals"),
    _trigger(
        "deal.updated",
        "Deal updated",
        "Any field on a deal changes. Stage moves have their own trigger.",
        "deal",
        "Deals",
        (_WATCHED_FIELDS,),
    ),
    _trigger(
        "deal.stage_changed",
        "Deal stage changed",
        "A deal moves between pipeline stages.",
        "deal",
        "Deals",
        (
            FieldSpec(
                key="to_stage_id",
                label="Only into this stage",
                kind="text",
                required=False,
                help_text="Leave blank to fire on any stage move.",
            ),
        ),
    ),
    # ------------------------------------------------------------- tasks
    _trigger("task.created", "Task created", "A task is created.", "task", "Tasks"),
    _trigger(
        "task.completed",
        "Task completed",
        "A task is finished.",
        "task",
        "Tasks",
    ),
    _trigger(
        "task.assigned",
        "Task assigned",
        "A task is handed to someone.",
        "task",
        "Tasks",
    ),
    # ------------------------------------------------------ notes, activity
    _trigger("note.created", "Note added", "A note is written on a record.", "note", "Notes"),
    _trigger(
        "activity.logged",
        "Activity logged",
        "A call, email, meeting or showing is logged.",
        "activity",
        "Activity",
        (
            FieldSpec(
                key="activity_type",
                label="Only this kind",
                kind="select",
                required=False,
                options=(
                    FieldOption("call", "Call"),
                    FieldOption("email", "Email"),
                    FieldOption("meeting", "Meeting"),
                    FieldOption("showing", "Showing"),
                    FieldOption("note", "Note"),
                ),
            ),
        ),
    ),
    # --------------------------------------------------------- messaging
    _trigger(
        "message.received",
        "Message received",
        "An inbound email or WhatsApp message arrives.",
        "conversation",
        "Messaging",
        (
            FieldSpec(
                key="channel",
                label="Only this channel",
                kind="select",
                required=False,
                options=(
                    FieldOption("email", "Email"),
                    FieldOption("whatsapp", "WhatsApp"),
                ),
            ),
        ),
    ),
    _trigger(
        "message.unmatched",
        "Message from an unknown contact",
        "An inbound message that matched no lead or client. The enquiry that "
        "would otherwise sit unfiled in a shared inbox.",
        "conversation",
        "Messaging",
    ),
    # ---------------------------------------------------------- calendar
    _trigger(
        "calendar.event_created",
        "Event scheduled",
        "A showing, closing or meeting is booked.",
        "calendar_event",
        "Calendar",
        (
            FieldSpec(
                key="event_type",
                label="Only this kind",
                kind="select",
                required=False,
                options=(
                    FieldOption("showing", "Showing"),
                    FieldOption("call", "Call"),
                    FieldOption("meeting", "Meeting"),
                    FieldOption("closing", "Closing"),
                    FieldOption("open_house", "Open house"),
                ),
            ),
        ),
    ),
    _trigger(
        "calendar.event_cancelled",
        "Event cancelled",
        "A booked event is cancelled.",
        "calendar_event",
        "Calendar",
    ),
)


def trigger_matches(
    definition: TriggerDefinition,
    config: dict[str, Any],
    payload: dict[str, Any],
) -> bool:
    """Whether a trigger's optional narrowing accepts this event.

    Runs before a run is created, so a workflow narrowed to one pipeline stage
    does not spawn a run per deal edit and immediately abandon it. The
    alternative — always create the run, let a condition end it — would fill
    the execution log with noise nobody wants to read.
    """
    if not config:
        return True

    if watched := config.get("fields"):
        changed = set(payload.get("changed_fields") or [])
        wanted = {name.strip() for name in str(watched).split(",") if name.strip()}
        if wanted and not (wanted & changed):
            return False

    # The remaining narrowings are all equality on one payload field, so they
    # share a loop rather than each getting a branch that could drift.
    for key in ("to_stage_id", "activity_type", "channel", "event_type"):
        expected = config.get(key)
        if expected and str(payload.get(key, "")) != str(expected):
            return False

    return True
