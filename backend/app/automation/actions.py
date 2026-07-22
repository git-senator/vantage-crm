"""Actions — what a workflow does.

Every action is a thin call into an existing service. That is the constraint the
whole phase is built around: an action must not be a second implementation of a
CRM operation with looser rules. `create_task` calls `TaskService`, so it gets
the same validation, the same activity on the record's timeline and the same
audit entry a person clicking the button would.

Two consequences worth stating.

**Actions inherit their service's permission checks.** The execution context
carries an authorization context, so an action a workflow's author could not
perform fails visibly rather than quietly succeeding with more access than
anyone has. See `app/automation/context.py`.

**Templating is deliberately trivial.** `{{lead.first_name}}` is substituted from
the run context and nothing else — no expressions, no filters, no loops. A
template language in a message that gets sent to a customer is an injection
surface and a support burden, and the moment it grows an `if` somebody will want
a `for`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.automation.registry import Definition, FieldOption, FieldSpec, build_registry

#: `{{ path.to.value }}` with optional whitespace. Anything else is left alone,
#: so a message containing braces for other reasons survives untouched.
_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


@dataclass(frozen=True, slots=True)
class ActionDefinition(Definition):
    """An action, plus what it needs from the trigger.

    `entity_types` restricts which triggers can reach it — "change deal stage"
    is meaningless under a `note.created` trigger, and the builder greys it out
    rather than letting somebody publish a workflow that can only ever fail.
    Empty means any.
    """

    entity_types: tuple[str, ...] = ()
    #: True when the action contacts somebody outside the workspace. The builder
    #: marks these, and the executor logs them at a higher level: an automation
    #: bug that files a task is embarrassing, one that emails four hundred
    #: clients is a different category of incident.
    external: bool = False


def _action(
    key: str,
    label: str,
    description: str,
    category: str,
    fields: tuple[FieldSpec, ...],
    *,
    entity_types: tuple[str, ...] = (),
    external: bool = False,
) -> ActionDefinition:
    return ActionDefinition(
        key=key,
        label=label,
        description=description,
        category=category,
        fields=fields,
        entity_types=entity_types,
        external=external,
    )


ACTIONS = build_registry(
    # ------------------------------------------------------------- work
    _action(
        "create_task",
        "Create a task",
        "Files a task against the triggering record, on the timeline like any "
        "other.",
        "Work",
        (
            FieldSpec("title", "Title", "template"),
            FieldSpec("description", "Description", "textarea", required=False),
            FieldSpec(
                "assignee",
                "Assign to",
                "select",
                options=(
                    FieldOption("record_owner", "Whoever owns the record"),
                    FieldOption("trigger_actor", "Whoever triggered this"),
                    FieldOption("specific_user", "A specific person"),
                ),
                default="record_owner",
            ),
            FieldSpec("assignee_id", "Person", "user", required=False),
            FieldSpec(
                "due_in_days",
                "Due in (days)",
                "number",
                required=False,
                help_text="Leave blank for no due date.",
            ),
            FieldSpec(
                "priority",
                "Priority",
                "select",
                required=False,
                options=(
                    FieldOption("low", "Low"),
                    FieldOption("medium", "Medium"),
                    FieldOption("high", "High"),
                    FieldOption("urgent", "Urgent"),
                ),
            ),
        ),
    ),
    _action(
        "update_record",
        "Update the record",
        "Sets fields on the record that triggered the workflow.",
        "Records",
        (
            FieldSpec(
                "updates",
                "Fields to set",
                "text",
                help_text="One per line, as field=value. Values may use {{...}}.",
            ),
        ),
    ),
    _action(
        "assign_user",
        "Assign to someone",
        "Hands the record to a person.",
        "Records",
        (
            FieldSpec("assignee_id", "Person", "user"),
        ),
        entity_types=("lead", "client", "property", "deal", "task"),
    ),
    _action(
        "change_deal_stage",
        "Move the deal",
        "Moves a deal to another pipeline stage, writing stage history exactly "
        "as a drag on the board does.",
        "Deals",
        (
            FieldSpec("stage_id", "Stage", "text"),
            FieldSpec("reason", "Note", "template", required=False),
        ),
        entity_types=("deal",),
    ),
    _action(
        "add_note",
        "Add a note",
        "Writes a note on the record.",
        "Records",
        (
            FieldSpec("title", "Title", "template", required=False),
            FieldSpec("body", "Note", "textarea"),
        ),
    ),
    # -------------------------------------------------------- messaging
    _action(
        "send_email",
        "Send an email",
        "Sends to the record's email address through the CRM's own inbox, so "
        "the reply threads back onto the record.",
        "Messaging",
        (
            FieldSpec("subject", "Subject", "template"),
            FieldSpec("body", "Message", "textarea"),
        ),
        entity_types=("lead", "client", "conversation"),
        external=True,
    ),
    _action(
        "send_whatsapp",
        "Send a WhatsApp message",
        "Only works inside the 24-hour session window; outside it WhatsApp "
        "rejects free-form text and the step fails with that reason.",
        "Messaging",
        (
            FieldSpec("body", "Message", "textarea"),
        ),
        entity_types=("lead", "client", "conversation"),
        external=True,
    ),
    _action(
        "create_notification",
        "Notify someone",
        "Raises an in-app notification. Whether it also becomes an email is "
        "the recipient's preference, not this workflow's decision.",
        "Messaging",
        (
            FieldSpec(
                "recipient",
                "Notify",
                "select",
                options=(
                    FieldOption("record_owner", "Whoever owns the record"),
                    FieldOption("trigger_actor", "Whoever triggered this"),
                    FieldOption("specific_user", "A specific person"),
                ),
                default="record_owner",
            ),
            FieldSpec("recipient_id", "Person", "user", required=False),
            FieldSpec("title", "Title", "template"),
            FieldSpec("body", "Detail", "textarea", required=False),
        ),
    ),
    # --------------------------------------------------------- calendar
    _action(
        "schedule_reminder",
        "Schedule a reminder",
        "Books a calendar event with a reminder, on the record's timeline.",
        "Calendar",
        (
            FieldSpec("title", "Title", "template"),
            FieldSpec("in_days", "In (days)", "number"),
            FieldSpec(
                "duration_minutes",
                "Length (minutes)",
                "number",
                required=False,
            ),
            FieldSpec(
                "reminder_minutes",
                "Remind before (minutes)",
                "number",
                required=False,
            ),
        ),
    ),
    # -------------------------------------------------------- integration
    _action(
        "call_webhook",
        "Call a webhook",
        "POSTs the run context to a URL. Egress is restricted to https and to "
        "hosts outside private networks — see the executor.",
        "Integration",
        (
            FieldSpec("url", "URL", "text"),
            FieldSpec(
                "secret",
                "Signing secret",
                "text",
                required=False,
                help_text=(
                    "When set, the body is signed with HMAC-SHA256 in "
                    "X-Vantage-Signature so the receiver can verify it."
                ),
            ),
        ),
        external=True,
    ),
)


# ------------------------------------------------------------- templating


def _lookup(path: str, context: dict[str, Any]) -> Any:
    """Walk a dotted path. Missing anywhere returns None, never raises."""
    current: Any = context
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def render_template(template: str, context: dict[str, Any]) -> str:
    """Substitute `{{path}}` from the run context.

    An unresolved placeholder renders as an empty string rather than being left
    literal. Leaving `{{lead.first_name}}` in a message a customer receives is
    worse than a missing word — it tells them they are being processed by a
    machine that is not working, which is the opposite of the point.
    """
    if not template:
        return ""

    def _replace(match: re.Match[str]) -> str:
        value = _lookup(match.group(1), context)
        return "" if value is None else str(value)

    return _PLACEHOLDER.sub(_replace, template)


def parse_field_updates(raw: str, context: dict[str, Any]) -> dict[str, str]:
    """Parse the `update_record` action's `field=value` lines.

    Deliberately line-based rather than JSON: the builder renders a textarea,
    and a JSON blob typed by hand is a syntax error waiting to fail validation
    at publish time for a reason nobody can see.
    """
    updates: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or "=" not in stripped:
            continue
        field, _, value = stripped.partition("=")
        key = field.strip()
        if key:
            updates[key] = render_template(value.strip(), context)
    return updates
