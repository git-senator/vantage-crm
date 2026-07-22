"""Action implementations.

Every handler is a thin call into an existing service. That is the constraint
the phase is built on: an action must not be a second implementation of a CRM
operation with looser rules. `create_task` goes through `TaskService`, so the
task lands on the record's timeline, produces an audit entry and honours the
same validation as one created by a person clicking the button.

**Failures are diagnosed, not raised raw.** `ActionFailedError` carries text written
for whoever reads the execution log — "this lead has no email address" rather
than a `NotFoundError` traceback. An automation that fails silently or fails
incomprehensibly is one nobody can fix.

**Every write is marked as automation-caused.** The services those writes go
through emit their own domain events, and without the marker a workflow that
updates a lead would trigger itself. See `app/automation/events.py`.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import socket
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.actions import ActionDefinition, parse_field_updates, render_template
from app.automation.context import ExecutionContext
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.models.client import Client
from app.models.lead import Lead
from app.schemas.calendar import CalendarEventCreate
from app.schemas.conversation import MessageSend
from app.schemas.note import NoteCreate
from app.schemas.task import TaskCreate
from app.services.calendar import CalendarService
from app.services.client import ClientService
from app.services.conversation import ConversationService
from app.services.deal import DealService
from app.services.lead import LeadService
from app.services.note import NoteService
from app.services.notification_center import NotificationCenter
from app.services.property import PropertyService
from app.services.task import TaskService

logger = get_logger(__name__)

#: How long a webhook may take. Short: a run is holding a worker while this is
#: in flight, and a receiver that needs more than ten seconds should accept and
#: process asynchronously like every other webhook in this system.
WEBHOOK_TIMEOUT_SECONDS = 10.0


class ActionFailedError(Exception):
    """A failure the action diagnosed, phrased for the execution log."""


# ------------------------------------------------------------ entity access


async def _require_entity(context: ExecutionContext) -> tuple[str, UUID]:
    if not context.entity_type or not context.entity_id:
        raise ActionFailedError("This step needs a record, and the trigger had none.")
    return context.entity_type, context.entity_id


def _resolve_person(
    context: ExecutionContext, config: dict[str, Any], *, key: str
) -> UUID:
    """Turn a `record_owner` / `trigger_actor` / `specific_user` choice into an id."""
    choice = config.get(key) or "record_owner"

    match choice:
        case "record_owner":
            owner = context.owner_id()
            if owner is None:
                raise ActionFailedError(
                    "This record has no owner, so there is nobody to assign to."
                )
            return owner
        case "trigger_actor":
            actor = context.actor_id
            if not actor:
                raise ActionFailedError(
                    "This change was made automatically, so there is no person "
                    "who triggered it."
                )
            return UUID(actor)
        case _:
            explicit = config.get(f"{key}_id")
            if not explicit:
                raise ActionFailedError("No person was chosen for this step.")
            try:
                return UUID(str(explicit))
            except ValueError as exc:
                raise ActionFailedError("The chosen person is not valid.") from exc


# ------------------------------------------------------------------ actions


async def _create_task(
    session: AsyncSession, config: dict[str, Any], context: ExecutionContext
) -> dict[str, Any]:
    entity_type, entity_id = await _require_entity(context)
    assignee = _resolve_person(context, config, key="assignee")

    due_at = None
    if (days := config.get("due_in_days")) is not None:
        due_at = datetime.now(UTC) + timedelta(days=float(days))

    # Tasks hang off a narrower vocabulary than triggers do. A note or a
    # conversation trigger cannot file a task against itself, so the task is
    # created unlinked rather than the step failing — an unlinked task in
    # somebody's queue is still the outcome the author wanted.
    linkable = entity_type in ("lead", "client", "property", "deal")

    task = await TaskService(session, context.auth).create_task(
        TaskCreate(
            title=render_template(str(config.get("title", "")), context.variables)
            or "Follow up",
            description=render_template(
                str(config.get("description") or ""), context.variables
            )
            or None,
            priority=config.get("priority") or "medium",
            due_at=due_at,
            entity_type=entity_type if linkable else None,
            entity_id=entity_id if linkable else None,
            assignee_id=assignee,
        ),
        context.acting_user,
    )
    return {"task_id": str(task.id), "title": task.title}


async def _update_record(
    session: AsyncSession, config: dict[str, Any], context: ExecutionContext
) -> dict[str, Any]:
    entity_type, entity_id = await _require_entity(context)
    updates = parse_field_updates(str(config.get("updates", "")), context.variables)
    if not updates:
        raise ActionFailedError("This step has no fields to set.")

    # Each entity's own update schema validates the payload, so a workflow
    # cannot set a field that does not exist or a status that is not in the
    # vocabulary — the same wall a malformed API request hits.
    from app.schemas.client import ClientUpdate
    from app.schemas.deal import DealUpdate
    from app.schemas.lead import LeadUpdate
    from app.schemas.property import PropertyUpdate

    try:
        match entity_type:
            case "lead":
                await LeadService(session, context.auth).update_lead(
                    entity_id, LeadUpdate(**updates), context.acting_user
                )
            case "client":
                await ClientService(session, context.auth).update_client(
                    entity_id, ClientUpdate(**updates), context.acting_user
                )
            case "property":
                await PropertyService(session, context.auth).update_property(
                    entity_id, PropertyUpdate(**updates), context.acting_user
                )
            case "deal":
                await DealService(session, context.auth).update_deal(
                    entity_id, DealUpdate(**updates), context.acting_user
                )
            case _:
                raise ActionFailedError(
                    f"A {entity_type} cannot be updated by a workflow."
                )
    except AppError as exc:
        raise ActionFailedError(str(exc)) from exc
    except ValueError as exc:
        # Pydantic rejected the values. The author configured a field that does
        # not exist or a value the schema refuses.
        raise ActionFailedError(f"Those values are not valid: {exc}") from exc

    return {"updated": sorted(updates)}


async def _assign_user(
    session: AsyncSession, config: dict[str, Any], context: ExecutionContext
) -> dict[str, Any]:
    entity_type, entity_id = await _require_entity(context)
    try:
        assignee = UUID(str(config.get("assignee_id")))
    except (TypeError, ValueError) as exc:
        raise ActionFailedError("No valid person was chosen.") from exc

    try:
        match entity_type:
            case "lead":
                await LeadService(session, context.auth).assign_lead(
                    entity_id, assignee, context.acting_user
                )
            case "client":
                await ClientService(session, context.auth).assign_client(
                    entity_id, assignee, context.acting_user
                )
            case "property":
                await PropertyService(session, context.auth).assign_property(
                    entity_id, assignee, context.acting_user
                )
            case "deal":
                await DealService(session, context.auth).assign_deal(
                    entity_id, assignee, context.acting_user
                )
            case "task":
                await TaskService(session, context.auth).assign_task(
                    entity_id, assignee, context.acting_user
                )
            case _:
                raise ActionFailedError(f"A {entity_type} cannot be assigned.")
    except AppError as exc:
        raise ActionFailedError(str(exc)) from exc

    return {"assignee_id": str(assignee)}


async def _change_deal_stage(
    session: AsyncSession, config: dict[str, Any], context: ExecutionContext
) -> dict[str, Any]:
    from app.schemas.deal import DealStageTransition

    entity_type, entity_id = await _require_entity(context)
    if entity_type != "deal":
        raise ActionFailedError("Only a deal can change stage.")

    try:
        stage_id = UUID(str(config.get("stage_id")))
    except (TypeError, ValueError) as exc:
        raise ActionFailedError("No valid stage was chosen.") from exc

    try:
        # Through `move_stage`, so stage history is written with the measured
        # time in the previous stage exactly as a drag on the board does. A
        # direct field write would silently break Phase 4 velocity reporting.
        deal = await DealService(session, context.auth).move_stage(
            entity_id,
            DealStageTransition(
                to_stage_id=stage_id,
                note=render_template(
                    str(config.get("reason") or ""), context.variables
                )
                or None,
            ),
            context.acting_user,
        )
    except AppError as exc:
        raise ActionFailedError(str(exc)) from exc

    return {"deal_id": str(deal.id), "stage_id": str(stage_id)}


async def _add_note(
    session: AsyncSession, config: dict[str, Any], context: ExecutionContext
) -> dict[str, Any]:
    entity_type, entity_id = await _require_entity(context)
    if entity_type not in ("lead", "client", "property", "deal", "task"):
        raise ActionFailedError(f"A note cannot be attached to a {entity_type}.")

    try:
        note = await NoteService(session, context.auth).create_note(
            NoteCreate(
                entity_type=entity_type,
                entity_id=entity_id,
                title=render_template(
                    str(config.get("title") or ""), context.variables
                )
                or None,
                body=render_template(str(config.get("body", "")), context.variables)
                or "(empty)",
            ),
            context.acting_user,
        )
    except AppError as exc:
        raise ActionFailedError(str(exc)) from exc

    return {"note_id": str(note.id)}


async def _contact_address(
    session: AsyncSession, context: ExecutionContext, *, field: str
) -> tuple[str, str | None]:
    """The triggering record's email or phone, plus a display name.

    Read from the snapshot first and only queried when absent, because the
    snapshot is what the conditions saw and re-reading could pick up a value
    that changed after the trigger.
    """
    record = context.record
    value = record.get(field)
    name = record.get("full_name") or record.get("display_name")

    if value:
        return str(value), (str(name) if name else None)

    entity_type, entity_id = await _require_entity(context)
    match entity_type:
        case "lead":
            query = (
                select(Lead)
                .where(Lead.id == entity_id)
                .where(Lead.organization_id == context.organization_id)
            )
        case "client":
            query = (
                select(Client)  # type: ignore[assignment]
                .where(Client.id == entity_id)
                .where(Client.organization_id == context.organization_id)
            )
        case _:
            raise ActionFailedError(f"A {entity_type} has no {field} to send to.")

    row = (await session.execute(query)).unique().scalar_one_or_none()
    if row is None or not getattr(row, field, None):
        raise ActionFailedError(f"This record has no {field}.")
    return str(getattr(row, field)), None


async def _send_email(
    session: AsyncSession, config: dict[str, Any], context: ExecutionContext
) -> dict[str, Any]:
    address, name = await _contact_address(session, context, field="email")

    try:
        # Through the conversation service, so the message lands in the shared
        # inbox and the customer's reply threads back onto the record instead
        # of arriving somewhere nobody is looking.
        message = await ConversationService(session, context.auth).send_message(
            MessageSend(
                channel="email",
                to_address=address,
                to_name=name,
                subject=render_template(
                    str(config.get("subject", "")), context.variables
                )
                or "A message from your agent",
                body_text=render_template(
                    str(config.get("body", "")), context.variables
                ),
                entity_type=(
                    context.entity_type
                    if context.entity_type in ("lead", "client", "deal")
                    else None
                ),
                entity_id=(
                    context.entity_id
                    if context.entity_type in ("lead", "client", "deal")
                    else None
                ),
            ),
            context.acting_user,
        )
    except AppError as exc:
        raise ActionFailedError(str(exc)) from exc

    return {"message_id": str(message.id), "to": address}


async def _send_whatsapp(
    session: AsyncSession, config: dict[str, Any], context: ExecutionContext
) -> dict[str, Any]:
    address, name = await _contact_address(session, context, field="phone")

    try:
        message = await ConversationService(session, context.auth).send_message(
            MessageSend(
                channel="whatsapp",
                to_address=address,
                to_name=name,
                body_text=render_template(
                    str(config.get("body", "")), context.variables
                ),
                entity_type=(
                    context.entity_type
                    if context.entity_type in ("lead", "client", "deal")
                    else None
                ),
                entity_id=(
                    context.entity_id
                    if context.entity_type in ("lead", "client", "deal")
                    else None
                ),
            ),
            context.acting_user,
        )
    except AppError as exc:
        raise ActionFailedError(str(exc)) from exc

    # Queued, not sent. The 24-hour session window is enforced by the channel
    # at delivery time, and the failure shows on the message in the thread.
    return {"message_id": str(message.id), "to": address, "status": "queued"}


async def _create_notification(
    session: AsyncSession, config: dict[str, Any], context: ExecutionContext
) -> dict[str, Any]:
    recipient = _resolve_person(context, config, key="recipient")

    notification = await NotificationCenter(session).raise_notification(
        organization_id=context.organization_id,
        recipient_id=recipient,
        # No actor: a machine decided this, which is what separates it in the
        # UI from "Sofia mentioned you".
        category="system",
        type="automation.notice",
        title=render_template(str(config.get("title", "")), context.variables)
        or "Automation",
        body=render_template(str(config.get("body") or ""), context.variables) or None,
        entity_type=context.entity_type,
        entity_id=context.entity_id,
        metadata={"workflow_id": str(context.workflow_id)},
    )
    return {
        "notification_id": str(notification.id) if notification else None,
        "recipient_id": str(recipient),
    }


async def _schedule_reminder(
    session: AsyncSession, config: dict[str, Any], context: ExecutionContext
) -> dict[str, Any]:
    starts_at = datetime.now(UTC) + timedelta(days=float(config.get("in_days", 1)))
    duration = int(config.get("duration_minutes") or 30)

    linkable = context.entity_type in ("lead", "client", "property", "deal")
    owner = context.owner_id()
    if owner is None:
        raise ActionFailedError(
            "This record has no owner, so there is no calendar to book on."
        )

    try:
        event, _conflicts = await CalendarService(session, context.auth).create_event(
            CalendarEventCreate(
                title=render_template(str(config.get("title", "")), context.variables)
                or "Follow up",
                event_type="call",
                starts_at=starts_at,
                ends_at=starts_at + timedelta(minutes=duration),
                reminder_minutes=(
                    int(config["reminder_minutes"])
                    if config.get("reminder_minutes") is not None
                    else None
                ),
                entity_type=context.entity_type if linkable else None,
                entity_id=context.entity_id if linkable else None,
                owner_id=owner,
            ),
            context.acting_user,
        )
    except AppError as exc:
        raise ActionFailedError(str(exc)) from exc

    # Conflicts are reported by the calendar, never enforced — and a workflow
    # is in no position to adjudicate one, so it books and moves on.
    return {"event_id": str(event.id), "starts_at": starts_at.isoformat()}


# ------------------------------------------------------------------ webhook


def _assert_egress_allowed(url: str) -> None:
    """Refuse anything that is not a public HTTPS endpoint.

    Without this, `call_webhook` is a server-side request forgery primitive with
    a form in the UI: an admin — or anyone who compromises one — could point a
    workflow at `http://169.254.169.254/` and read cloud instance credentials,
    or at an internal service that trusts its network position.

    Resolution happens here, before the request, and every resolved address is
    checked. Checking only the hostname loses to a DNS record that points at a
    private address, which is the standard bypass.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ActionFailedError("Webhooks must use https.")
    if not parsed.hostname:
        raise ActionFailedError("That webhook URL is not valid.")

    try:
        resolved = socket.getaddrinfo(parsed.hostname, parsed.port or 443)
    except socket.gaierror as exc:
        raise ActionFailedError("That webhook host could not be resolved.") from exc

    for entry in resolved:
        address = ipaddress.ip_address(entry[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
        ):
            logger.warning(
                "webhook_egress_blocked", extra={"host": parsed.hostname}
            )
            raise ActionFailedError(
                "That webhook points inside a private network, which is not "
                "allowed."
            )


async def _call_webhook(
    session: AsyncSession, config: dict[str, Any], context: ExecutionContext
) -> dict[str, Any]:
    url = str(config.get("url", "")).strip()
    _assert_egress_allowed(url)

    body = {
        "workflow_id": str(context.workflow_id),
        "run_id": str(context.run_id),
        "entity_type": context.entity_type,
        "entity_id": str(context.entity_id) if context.entity_id else None,
        "record": context.record,
    }

    headers = {"Content-Type": "application/json"}
    if secret := config.get("secret"):
        import json

        raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
        headers["X-Vantage-Signature"] = hmac.new(
            str(secret).encode(), raw, hashlib.sha256
        ).hexdigest()

    try:
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise ActionFailedError(f"The webhook could not be reached: {exc}") from exc

    if response.status_code >= 400:
        raise ActionFailedError(
            f"The webhook returned {response.status_code}."
        )

    return {"status_code": response.status_code}


# ------------------------------------------------------------------ dispatch

_HANDLERS = {
    "create_task": _create_task,
    "update_record": _update_record,
    "assign_user": _assign_user,
    "change_deal_stage": _change_deal_stage,
    "add_note": _add_note,
    "send_email": _send_email,
    "send_whatsapp": _send_whatsapp,
    "create_notification": _create_notification,
    "schedule_reminder": _schedule_reminder,
    "call_webhook": _call_webhook,
}


async def run_action(
    session: AsyncSession,
    definition: ActionDefinition,
    config: dict[str, Any],
    context: ExecutionContext,
) -> dict[str, Any]:
    handler = _HANDLERS.get(definition.key)
    if handler is None:  # pragma: no cover — the registry and this map agree
        raise ActionFailedError(f"{definition.label} is not implemented.")
    return await handler(session, config, context)
