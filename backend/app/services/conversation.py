"""Conversations: sending, receiving, threading and filing.

The two paths are deliberately asymmetric, because their trust levels are.

**Outbound** starts from an authenticated user who already passed a permission
check. It writes a `queued` message inside the caller's transaction and enqueues
delivery — the request does not wait on a mail provider, and a provider outage
becomes a `failed` message rather than a failed request.

**Inbound** starts from a webhook. Nothing about it is trusted: it is
deduplicated against the provider id, the address is normalised before it is
used as an identity, and matching to a CRM record is a *lookup*, never something
the payload can assert. A message from a stranger lands in an unmatched thread
rather than being dropped — an inbound enquiry that does not fit the schema is
still an inbound enquiry.

**Record matching is by address, one hop, no guessing.** The email matches a
lead or a client with that address, or it matches nothing. Fuzzy matching on
name would file a stranger's mail onto a customer's record, which is worse than
leaving it unfiled — and unfiled is visible and fixable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models.client import Client
from app.models.conversation import Conversation, Message
from app.models.lead import Lead
from app.models.user import User
from app.repositories.conversation import ConversationRepository, MessageRepository
from app.schemas.common import Cursor
from app.schemas.conversation import (
    ConversationFilters,
    ConversationUpdate,
    InboundEmailPayload,
    MessageSend,
)
from app.services.audit import AuditService
from app.services.messaging import InboundMessage, MessagingError, build_channel
from app.services.notification_center import NotificationCenter
from app.services.rbac import AuthorizationContext, RbacService
from app.workers.queue import JobName, enqueue

logger = get_logger(__name__)

ENTITY_TYPE = "message"

#: How much of a message body becomes the inbox preview.
PREVIEW_LENGTH = 200


def _preview(body: str) -> str:
    collapsed = " ".join(body.split())
    return collapsed[:PREVIEW_LENGTH]


class ConversationService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.conversations = ConversationRepository(session)
        self.messages = MessageRepository(session)
        self.audit = AuditService(session)
        self.rbac = RbacService(session)

    # --------------------------------------------------------------- scope

    async def _owner_ids(self, permission: str) -> list[UUID] | None:
        scope = self.auth.require(permission)
        return await self.rbac.owner_ids_for_scope(self.auth, scope)

    # ---------------------------------------------------------------- read

    async def list_conversations(
        self, *, filters: ConversationFilters, limit: int, cursor: Cursor | None
    ) -> tuple[list[Conversation], bool]:
        owner_ids = await self._owner_ids("contacts.view")
        return await self.conversations.list_page(
            self.auth.organization_id,
            filters=filters,
            owner_ids=owner_ids,
            limit=limit,
            cursor=cursor,
        )

    async def get_conversation(self, conversation_id: UUID) -> Conversation:
        owner_ids = await self._owner_ids("contacts.view")
        conversation = await self.conversations.get_visible(
            conversation_id, self.auth.organization_id, owner_ids
        )
        if conversation is None:
            raise NotFoundError("Conversation not found.")
        return conversation

    async def list_messages(self, conversation_id: UUID) -> list[Message]:
        # Readability of the thread is proved first; messages carry no scope of
        # their own, exactly as notes and activities do not.
        await self.get_conversation(conversation_id)
        return await self.messages.list_for_conversation(
            conversation_id, self.auth.organization_id
        )

    async def unread_total(self) -> int:
        owner_ids = await self._owner_ids("contacts.view")
        return await self.conversations.total_unread(
            self.auth.organization_id, owner_ids
        )

    # --------------------------------------------------------------- write

    async def send_message(self, payload: MessageSend, actor: User) -> Message:
        """Queue an outbound message, creating the thread if there isn't one.

        The message row is written *before* anything is sent, in the caller's
        transaction, with `status='queued'`. That ordering is the point: if the
        enqueue is lost or the provider is down, there is still a record that a
        person tried to send this, visible in the thread as queued or failed. A
        send-then-record order loses the message entirely on the same failure.
        """
        self.auth.require("contacts.manage")

        channel = build_channel(payload.channel)
        address = channel.normalise_address(payload.to_address)
        if not address:
            raise ConflictError("A recipient address is required.")

        conversation = await self._find_or_create(
            channel=payload.channel,
            external_id=address,
            display_name=payload.to_name,
            subject=payload.subject,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            owner_id=actor.id,
        )

        message = Message(
            organization_id=self.auth.organization_id,
            conversation_id=conversation.id,
            direction="outbound",
            status="queued",
            sender_id=actor.id,
            from_address=actor.email,
            to_address=address,
            subject=payload.subject or conversation.subject,
            body_text=payload.body_text,
        )
        self.session.add(message)
        await self.session.flush()

        conversation.last_message_at = message.created_at or datetime.now(UTC)
        conversation.last_message_preview = _preview(payload.body_text)
        if conversation.owner_id is None:
            # Replying claims an unowned thread. Somebody answering is the
            # clearest possible signal of who owns it.
            conversation.owner_id = actor.id
        await self.session.flush()

        await enqueue(
            JobName.DELIVER_MESSAGE,
            str(message.id),
            str(self.auth.organization_id),
            job_id=f"message:{message.id}",
        )

        await self.audit.record(
            action=AuditAction.MESSAGE_SENT,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=message.id,
            metadata={
                "channel": payload.channel,
                "to": address,
                "conversation_id": str(conversation.id),
            },
        )
        logger.info(
            "message_queued",
            extra={"message_id": str(message.id), "channel": payload.channel},
        )
        return message

    async def update_conversation(
        self, conversation_id: UUID, payload: ConversationUpdate, actor: User
    ) -> Conversation:
        """File a thread against a record, or pin it.

        Filing is the manual counterpart to ingestion's automatic match — the
        answer to "this enquiry is actually about the Harrison listing" that no
        address lookup could have found.
        """
        self.auth.require("contacts.manage")
        conversation = await self.get_conversation(conversation_id)

        updates = payload.model_dump(exclude_unset=True)
        if "entity_type" in updates or "entity_id" in updates:
            entity_type = updates.get("entity_type", conversation.entity_type)
            entity_id = updates.get("entity_id", conversation.entity_id)
            if (entity_type is None) != (entity_id is None):
                raise ConflictError(
                    "A conversation is filed against a record or against "
                    "nothing — not half of one."
                )
            conversation.entity_type = entity_type
            conversation.entity_id = entity_id

        if "is_pinned" in updates:
            conversation.is_pinned = bool(updates["is_pinned"])

        await self.session.flush()
        return conversation

    async def mark_read(self, conversation_id: UUID, actor: User) -> Conversation:
        """Clear the thread's unread count and stamp its inbound messages.

        Read state is per *workspace*, not per user: a thread one colleague has
        answered is not still unread for everybody else. That is the right model
        for a shared inbox and the wrong one for personal mail — which is why
        notifications, which are personal, track read state per recipient.
        """
        conversation = await self.get_conversation(conversation_id)

        now = datetime.now(UTC)
        unread = (
            (
                await self.session.execute(
                    select(Message)
                    .where(Message.conversation_id == conversation.id)
                    .where(Message.direction == "inbound")
                    .where(Message.read_at.is_(None))
                )
            )
            .unique()
            .scalars()
            .all()
        )
        for message in unread:
            message.read_at = now

        conversation.unread_count = 0
        await self.session.flush()
        return conversation

    # ------------------------------------------------------------ internal

    async def _find_or_create(
        self,
        *,
        channel: str,
        external_id: str,
        display_name: str | None,
        subject: str | None,
        entity_type: str | None,
        entity_id: UUID | None,
        owner_id: UUID | None,
    ) -> Conversation:
        existing = await self.conversations.find_by_identity(
            self.auth.organization_id, channel=channel, external_id=external_id
        )
        if existing is not None:
            # Never overwrite an existing filing from here. A thread already
            # attached to a client must not be silently re-pointed because
            # somebody composed from a different record's page.
            if existing.entity_type is None and entity_type is not None:
                existing.entity_type = entity_type
                existing.entity_id = entity_id
            if display_name and not existing.display_name:
                existing.display_name = display_name
            return existing

        conversation = Conversation(
            organization_id=self.auth.organization_id,
            channel=channel,
            external_id=external_id,
            display_name=display_name,
            subject=subject,
            entity_type=entity_type,
            entity_id=entity_id,
            owner_id=owner_id,
        )
        self.session.add(conversation)
        await self.session.flush()
        return conversation


class InboundMessageService:
    """Ingestion. Runs with no user — a webhook has no session.

    Separate from `ConversationService` because it answers to different rules:
    it has no `AuthorizationContext`, it takes an organization id resolved from
    the webhook's own authentication, and every one of its inputs is hostile.
    """

    def __init__(self, session: AsyncSession, organization_id: UUID) -> None:
        self.session = session
        self.organization_id = organization_id
        self.conversations = ConversationRepository(session)
        self.messages = MessageRepository(session)

    async def ingest_email(self, payload: InboundEmailPayload) -> tuple[Message, bool]:
        """File an inbound email.

        A thin translation into `ingest_channel_message`: the pipeline —
        dedupe, thread, match, notify — is identical for every channel, and
        writing it twice is how the two versions of "already ingested" end up
        meaning different things.
        """
        return await self.ingest_channel_message(
            InboundMessage(
                channel="email",
                from_address=payload.from_address,
                from_name=payload.from_name,
                to_address=payload.to_address,
                body_text=payload.body_text,
                provider_message_id=payload.provider_message_id,
                subject=payload.subject,
                body_html=payload.body_html,
                rfc_message_id=payload.rfc_message_id,
                in_reply_to=payload.in_reply_to,
                metadata={k: str(v) for k, v in payload.metadata.items()},
            )
        )

    async def ingest_channel_message(
        self, inbound: InboundMessage
    ) -> tuple[Message, bool]:
        """File an inbound message on any channel. Returns (message, created).

        `created=False` means this was a replay of one already ingested, which
        every provider sends eventually. Deduplication is on the provider's own
        id and is the first thing that happens, before any write — for WhatsApp
        it is the *only* replay protection there is, since Meta signs no
        timestamp.
        """
        channel = build_channel(inbound.channel)
        address = channel.normalise_address(inbound.from_address)
        if not address:
            raise ConflictError("An inbound message needs a sender address.")

        duplicate = await self.messages.find_by_provider_id(
            self.organization_id, inbound.provider_message_id
        )
        if duplicate is not None:
            logger.info(
                "inbound_message_duplicate",
                extra={
                    "provider_message_id": inbound.provider_message_id,
                    "channel": inbound.channel,
                },
            )
            return duplicate, False

        conversation = await self.conversations.find_by_identity(
            self.organization_id, channel=inbound.channel, external_id=address
        )
        if conversation is None:
            matched_type, matched_id, owner_id = await self._match_record(
                inbound.channel, address
            )
            conversation = Conversation(
                organization_id=self.organization_id,
                channel=inbound.channel,
                external_id=address,
                display_name=inbound.from_name,
                subject=inbound.subject,
                entity_type=matched_type,
                entity_id=matched_id,
                # The record's owner inherits the thread. Nobody owns a message
                # from a stranger, and `_scoped` makes unowned threads visible
                # to everyone precisely so it does not sit unanswered.
                owner_id=owner_id,
            )
            self.session.add(conversation)
            await self.session.flush()
        elif inbound.from_name and not conversation.display_name:
            conversation.display_name = inbound.from_name

        message = Message(
            organization_id=self.organization_id,
            conversation_id=conversation.id,
            direction="inbound",
            status="received",
            from_address=address,
            to_address=inbound.to_address.strip().lower(),
            subject=inbound.subject,
            body_text=inbound.body_text,
            body_html=inbound.body_html,
            provider_message_id=inbound.provider_message_id,
            rfc_message_id=inbound.rfc_message_id,
            in_reply_to=inbound.in_reply_to,
            sent_at=datetime.now(UTC),
            metadata_=dict(inbound.metadata),
        )
        self.session.add(message)
        await self.session.flush()

        conversation.last_message_at = datetime.now(UTC)
        conversation.last_message_preview = _preview(inbound.body_text)
        conversation.unread_count += 1
        await self.session.flush()

        if conversation.owner_id is not None:
            await NotificationCenter(self.session).raise_notification(
                organization_id=self.organization_id,
                recipient_id=conversation.owner_id,
                category="mention",
                type="message.received",
                title=f"New message from {conversation.display_name or address}",
                body=_preview(inbound.body_text),
                entity_type=conversation.entity_type,
                entity_id=conversation.entity_id,
                metadata={"conversation_id": str(conversation.id)},
            )

        logger.info(
            "inbound_message_ingested",
            extra={
                "message_id": str(message.id),
                "channel": inbound.channel,
                "matched": conversation.entity_type is not None,
            },
        )
        return message, True

    async def _match_record(
        self, channel: str, address: str
    ) -> tuple[str | None, UUID | None, UUID | None]:
        """Find the lead or client this address belongs to.

        Exact match only, and leads before clients — a lead is the newer, more
        actively worked record, so an address on both is far likelier to be
        about the lead. Returns the record's owner too, so the thread lands with
        the person already responsible for the relationship.

        The column and the comparison depend on the channel. Email compares
        `lower()` on both sides, because the stored address was typed by a
        person while the inbound one is normalised. Phone strips every
        non-digit from the stored value, because a CRM's phone column contains
        every spelling a human has ever used — `+1 (415) 555-0100`,
        `415-555-0100`, `4155550100` — and matching raw would fail on all but
        one of them.

        No fuzzy matching, deliberately. Filing a stranger's message onto a
        customer's record is worse than leaving it unfiled, and unfiled is
        visible and fixable.
        """
        if channel == "email":
            lead_column = func.lower(Lead.email)
            client_column = func.lower(Client.email)
        else:
            # Postgres regexp_replace with the 'g' flag: strip everything that
            # is not a digit, matching how the channel normalises the inbound
            # address. Unindexed and deliberately so — this runs once per new
            # conversation, not per message, over a tenant's contact list.
            lead_column = func.regexp_replace(Lead.phone, r"\D", "", "g")
            client_column = func.regexp_replace(Client.phone, r"\D", "", "g")

        lead = (
            (
                await self.session.execute(
                    select(Lead)
                    .where(Lead.organization_id == self.organization_id)
                    .where(Lead.deleted_at.is_(None))
                    .where(lead_column == address)
                    .order_by(Lead.created_at.desc())
                    .limit(1)
                )
            )
            .unique()
            .scalar_one_or_none()
        )
        if lead is not None:
            return "lead", lead.id, lead.owner_id

        client = (
            (
                await self.session.execute(
                    select(Client)
                    .where(Client.organization_id == self.organization_id)
                    .where(Client.deleted_at.is_(None))
                    .where(client_column == address)
                    .order_by(Client.created_at.desc())
                    .limit(1)
                )
            )
            .unique()
            .scalar_one_or_none()
        )
        if client is not None:
            return "client", client.id, client.owner_id

        return None, None, None


__all__ = ["ConversationService", "InboundMessageService", "MessagingError"]
