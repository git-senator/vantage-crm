"""Conversations — threading, record matching, and the inbound webhook.

The tests that matter most are in `TestInboundIngestion` and
`TestWebhookAuthentication`, because inbound mail is the only path in this
system where **every input is hostile and there is no authenticated user
behind it**. What they pin down:

  * a replay does not duplicate a message — every provider sends one eventually;
  * an address matches a record by exact lookup or matches nothing, never by
    guessing;
  * mail from a stranger is kept rather than dropped;
  * an unsigned, mis-signed, stale or cross-tenant webhook is refused.

`TestOutbound` covers the ordering that makes a send survivable: the message row
exists before anything is sent, so a provider outage is a failed message in the
thread rather than a request that lost what the user typed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.exceptions import AuthenticationError, ConflictError, NotFoundError
from app.models.audit import AuditLog
from app.models.conversation import Conversation, Message
from app.models.notification import Notification
from app.schemas.client import ClientCreate
from app.schemas.conversation import (
    ConversationFilters,
    ConversationUpdate,
    InboundEmailPayload,
    MessageSend,
)
from app.schemas.lead import LeadCreate
from app.services.client import ClientService
from app.services.conversation import ConversationService, InboundMessageService
from app.services.lead import LeadService
from app.services.messaging import EmailChannel
from tests.conftest import auth_for, make_user

pytestmark = pytest.mark.integration


def _inbound(**overrides) -> InboundEmailPayload:  # type: ignore[no-untyped-def]
    data = {
        "from_address": "Sana.Kaur@Example.com",
        "from_name": "Sana Kaur",
        "to_address": "inbox@vantagerealty.example",
        "subject": "Question about 88 Townsend",
        "body_text": "Is the penthouse still available?",
        "provider_message_id": f"provider-{uuid4()}",
        **overrides,
    }
    return InboundEmailPayload(**data)


class TestAddressNormalisation:
    """The identity function for a thread. Get it wrong and one person gets
    two conversations."""

    def test_case_and_display_name_are_stripped(self) -> None:
        channel = EmailChannel()
        assert (
            channel.normalise_address('"Sana Kaur" <Sana@Example.com>')
            == "sana@example.com"
        )
        assert channel.normalise_address("  SANA@example.com ") == "sana@example.com"

    def test_normalisation_is_idempotent(self) -> None:
        channel = EmailChannel()
        once = channel.normalise_address("Sana@Example.com")
        assert channel.normalise_address(once) == once


class TestInboundIngestion:
    async def test_a_stranger_lands_in_an_unmatched_thread(
        self, db: AsyncSession, organization
    ) -> None:  # type: ignore[no-untyped-def]
        """Kept, not dropped. An inbound enquiry that does not fit the schema is
        still an inbound enquiry."""
        message, created = await InboundMessageService(
            db, organization.id
        ).ingest_email(_inbound())

        assert created is True
        conversation = (
            (await db.execute(select(Conversation))).unique().scalar_one()
        )
        assert conversation.entity_type is None
        assert conversation.owner_id is None
        assert conversation.unread_count == 1
        assert conversation.external_id == "sana.kaur@example.com"
        assert message.direction == "inbound"
        assert message.status == "received"

    async def test_a_matching_lead_is_found_and_its_owner_inherits_the_thread(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        lead = await LeadService(db, auth).create_lead(
            LeadCreate(
                first_name="Sana", last_name="Kaur", email="Sana.Kaur@Example.com"
            ),
            user,
        )

        await InboundMessageService(db, organization.id).ingest_email(_inbound())

        conversation = (
            (await db.execute(select(Conversation))).unique().scalar_one()
        )
        assert conversation.entity_type == "lead"
        assert conversation.entity_id == lead.id
        # The thread lands with whoever already owns the relationship.
        assert conversation.owner_id == lead.owner_id

    async def test_matching_is_case_insensitive_on_both_sides(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """The stored address was typed by a person; the inbound one is
        normalised. A raw comparison would file real customer mail as coming
        from a stranger."""
        user, auth = admin
        await LeadService(db, auth).create_lead(
            LeadCreate(
                first_name="Sana", last_name="Kaur", email="SANA.KAUR@EXAMPLE.COM"
            ),
            user,
        )

        await InboundMessageService(db, organization.id).ingest_email(
            _inbound(from_address="sana.kaur@example.com")
        )

        conversation = (
            (await db.execute(select(Conversation))).unique().scalar_one()
        )
        assert conversation.entity_type == "lead"

    async def test_a_client_matches_when_no_lead_does(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        client = await ClientService(db, auth).create_client(
            ClientCreate(
                first_name="Omar", last_name="Haddad", email="omar@example.com"
            ),
            user,
        )

        await InboundMessageService(db, organization.id).ingest_email(
            _inbound(from_address="omar@example.com")
        )

        conversation = (
            (await db.execute(select(Conversation))).unique().scalar_one()
        )
        assert conversation.entity_type == "client"
        assert conversation.entity_id == client.id

    async def test_a_replay_does_not_duplicate(
        self, db: AsyncSession, organization
    ) -> None:  # type: ignore[no-untyped-def]
        """Every provider replays webhooks eventually. Dedupe is on the
        provider's own id and happens before any write."""
        payload = _inbound()
        service = InboundMessageService(db, organization.id)

        first, created_first = await service.ingest_email(payload)
        second, created_second = await service.ingest_email(payload)

        assert created_first is True
        assert created_second is False
        assert first.id == second.id
        assert len((await db.execute(select(Message))).unique().scalars().all()) == 1
        conversation = (
            (await db.execute(select(Conversation))).unique().scalar_one()
        )
        # And the unread count did not double-count the replay.
        assert conversation.unread_count == 1

    async def test_a_second_message_appends_to_the_same_thread(
        self, db: AsyncSession, organization
    ) -> None:  # type: ignore[no-untyped-def]
        service = InboundMessageService(db, organization.id)
        await service.ingest_email(_inbound())
        await service.ingest_email(_inbound(body_text="Following up on this."))

        conversations = (
            (await db.execute(select(Conversation))).unique().scalars().all()
        )
        assert len(conversations) == 1
        assert conversations[0].unread_count == 2
        assert conversations[0].last_message_preview == "Following up on this."

    async def test_the_owner_is_notified(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Sana", last_name="Kaur", email="sana@example.com"),
            user,
        )

        await InboundMessageService(db, organization.id).ingest_email(
            _inbound(from_address="sana@example.com")
        )

        notification = (
            (await db.execute(select(Notification))).unique().scalar_one()
        )
        assert notification.type == "message.received"
        assert notification.user_id == user.id

    async def test_an_unowned_thread_notifies_nobody(
        self, db: AsyncSession, organization
    ) -> None:  # type: ignore[no-untyped-def]
        """There is nobody to notify, and picking someone arbitrarily would be
        worse than the inbox badge doing its job."""
        await InboundMessageService(db, organization.id).ingest_email(_inbound())
        assert (await db.execute(select(Notification))).unique().scalars().all() == []


class TestOutbound:
    async def test_sending_records_the_message_before_delivering_it(
        self, db: AsyncSession, admin, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        """If the enqueue is lost or the provider is down there is still a
        record that a person tried to send this. A send-then-record order loses
        the message entirely on the same failure."""
        queued: list[tuple] = []

        async def _capture(job_name: str, *args: object, **kwargs: object) -> str:
            queued.append((job_name, args))
            return "job-id"

        monkeypatch.setattr("app.services.conversation.enqueue", _capture)

        user, auth = admin
        message = await ConversationService(db, auth).send_message(
            MessageSend(
                to_address="Buyer@Example.com",
                to_name="A Buyer",
                subject="Following up",
                body_text="Are you free on Thursday?",
            ),
            user,
        )

        assert message.status == "queued"
        assert message.sent_at is None
        assert message.to_address == "buyer@example.com"
        assert queued and queued[0][0] == "deliver_message"

    async def test_sending_twice_reuses_the_thread(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = ConversationService(db, auth)
        payload = MessageSend(
            to_address="buyer@example.com", subject="Hello", body_text="First."
        )
        await service.send_message(payload, user)
        await service.send_message(
            MessageSend(
                to_address="BUYER@example.com", subject="Hello", body_text="Second."
            ),
            user,
        )

        conversations = (
            (await db.execute(select(Conversation))).unique().scalars().all()
        )
        assert len(conversations) == 1
        assert conversations[0].last_message_preview == "Second."

    async def test_sending_audits(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """The intent to contact a customer is the auditable act; whether the
        provider accepted it is a status on the message."""
        user, auth = admin
        await ConversationService(db, auth).send_message(
            MessageSend(
                to_address="buyer@example.com", subject="Hi", body_text="Hello."
            ),
            user,
        )

        entry = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.MESSAGE_SENT)
            )
        ).scalar_one()
        assert entry.metadata_["to"] == "buyer@example.com"

    async def test_replying_claims_an_unowned_thread(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Somebody answering is the clearest possible signal of who owns it."""
        await InboundMessageService(db, organization.id).ingest_email(_inbound())
        user, auth = admin

        await ConversationService(db, auth).send_message(
            MessageSend(
                to_address="sana.kaur@example.com",
                subject="Re: Question",
                body_text="Yes, it is.",
            ),
            user,
        )

        conversation = (
            (await db.execute(select(Conversation))).unique().scalar_one()
        )
        assert conversation.owner_id == user.id

    async def test_composing_never_repoints_an_existing_filing(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """A thread already attached to a client must not be silently
        re-pointed because somebody composed from a different record's page."""
        user, auth = admin
        client = await ClientService(db, auth).create_client(
            ClientCreate(
                first_name="Omar", last_name="Haddad", email="omar@example.com"
            ),
            user,
        )
        await InboundMessageService(db, organization.id).ingest_email(
            _inbound(from_address="omar@example.com")
        )

        await ConversationService(db, auth).send_message(
            MessageSend(
                to_address="omar@example.com",
                subject="Hi",
                body_text="Hello.",
                entity_type="lead",
                entity_id=uuid4(),
            ),
            user,
        )

        conversation = (
            (await db.execute(select(Conversation))).unique().scalar_one()
        )
        assert conversation.entity_type == "client"
        assert conversation.entity_id == client.id


class TestReadingAndFiling:
    async def test_marking_read_clears_the_thread_for_everyone(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Read state is per workspace: a thread a colleague answered is not
        still unread for everybody else."""
        await InboundMessageService(db, organization.id).ingest_email(_inbound())
        user, auth = admin
        service = ConversationService(db, auth)
        conversation = (
            (await db.execute(select(Conversation))).unique().scalar_one()
        )

        cleared = await service.mark_read(conversation.id, user)

        assert cleared.unread_count == 0
        message = (await db.execute(select(Message))).unique().scalar_one()
        assert message.read_at is not None
        assert await service.unread_total() == 0

    async def test_filing_half_a_record_is_refused(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        await InboundMessageService(db, organization.id).ingest_email(_inbound())
        user, auth = admin
        conversation = (
            (await db.execute(select(Conversation))).unique().scalar_one()
        )

        with pytest.raises(ConflictError, match="half"):
            await ConversationService(db, auth).update_conversation(
                conversation.id, ConversationUpdate(entity_type="lead"), user
            )

    async def test_filing_by_hand_works(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """The answer to "this enquiry is actually about the Harrison listing"
        that no address lookup could have found."""
        await InboundMessageService(db, organization.id).ingest_email(_inbound())
        user, auth = admin
        lead = await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Someone", last_name="Else"), user
        )
        conversation = (
            (await db.execute(select(Conversation))).unique().scalar_one()
        )

        filed = await ConversationService(db, auth).update_conversation(
            conversation.id,
            ConversationUpdate(entity_type="lead", entity_id=lead.id),
            user,
        )
        assert filed.entity_id == lead.id


class TestVisibility:
    async def test_an_unclaimed_thread_is_visible_to_everyone(
        self, db: AsyncSession, organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        """Hiding unclaimed mail from everybody is how an enquiry sits
        unanswered for a week."""
        await InboundMessageService(db, organization.id).ingest_email(_inbound())
        agent = await make_user(db, organization, "agent@vantage.example")
        agent_auth = await auth_for(db, agent, "agent")

        rows, _more = await ConversationService(db, agent_auth).list_conversations(
            filters=ConversationFilters(), limit=10, cursor=None
        )
        assert len(rows) == 1

    async def test_another_agents_claimed_thread_is_not_visible(
        self, db: AsyncSession, organization, admin, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await ConversationService(db, auth).send_message(
            MessageSend(
                to_address="buyer@example.com", subject="Hi", body_text="Hello."
            ),
            user,
        )

        agent = await make_user(db, organization, "agent@vantage.example")
        agent_auth = await auth_for(db, agent, "agent")
        rows, _more = await ConversationService(db, agent_auth).list_conversations(
            filters=ConversationFilters(), limit=10, cursor=None
        )
        assert rows == []

    async def test_getting_another_agents_thread_is_a_404(
        self, db: AsyncSession, organization, admin, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        message = await ConversationService(db, auth).send_message(
            MessageSend(
                to_address="buyer@example.com", subject="Hi", body_text="Hello."
            ),
            user,
        )

        agent = await make_user(db, organization, "agent@vantage.example")
        agent_auth = await auth_for(db, agent, "agent")
        with pytest.raises(NotFoundError):
            await ConversationService(db, agent_auth).get_conversation(
                message.conversation_id
            )


class TestWebhookAuthentication:
    """The one endpoint with no authenticated user behind it."""

    @staticmethod
    def _sign(secret: str, timestamp: str, organization: str, body: bytes) -> str:
        payload = b".".join([timestamp.encode(), organization.encode(), body])
        return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    async def _verify(self, secret_value: str, **overrides):  # type: ignore[no-untyped-def]
        from app.api.v1.webhooks import verify_inbound_signature
        from app.core.config import Settings

        organization = str(uuid4())
        body = json.dumps({"hello": "world"}).encode()
        timestamp = str(int(time.time()))
        settings = Settings(  # type: ignore[call-arg]
            _env_file=None,
            JWT_SECRET="x" * 32,
            INBOUND_WEBHOOK_SECRET=secret_value,
        )

        class _Request:
            async def body(self) -> bytes:
                return overrides.get("body", body)

        return await verify_inbound_signature(
            _Request(),  # type: ignore[arg-type]
            settings,
            x_vantage_signature=overrides.get(
                "signature",
                self._sign("shared-secret", timestamp, organization, body),
            ),
            x_vantage_timestamp=overrides.get("timestamp", timestamp),
            x_vantage_organization=overrides.get("organization", organization),
        )

    async def test_a_valid_signature_yields_the_tenant(self) -> None:
        assert await self._verify("shared-secret") is not None

    async def test_an_unset_secret_refuses_everything(self) -> None:
        """A default-open check on an unconfigured secret would accept
        anything that posted."""
        with pytest.raises(AuthenticationError, match="not configured"):
            await self._verify("")

    async def test_a_wrong_signature_is_refused(self) -> None:
        with pytest.raises(AuthenticationError, match="signature"):
            await self._verify("shared-secret", signature="0" * 64)

    async def test_missing_headers_are_refused(self) -> None:
        with pytest.raises(AuthenticationError, match="Missing"):
            await self._verify("shared-secret", signature=None)

    async def test_a_stale_timestamp_is_refused(self) -> None:
        """Without a bounded timestamp a captured request replays forever."""
        stale = str(int(time.time()) - 3600)
        with pytest.raises(AuthenticationError, match="window"):
            await self._verify("shared-secret", timestamp=stale)

    async def test_a_body_that_changed_after_signing_is_refused(self) -> None:
        """The signature covers the raw bytes, so tampering invalidates it."""
        with pytest.raises(AuthenticationError, match="signature"):
            await self._verify("shared-secret", body=b'{"hello":"tampered"}')

    async def test_retargeting_at_another_tenant_is_refused(self) -> None:
        """The tenant is inside the signature, so a validly-signed body cannot
        be pointed at somebody else's data."""
        with pytest.raises(AuthenticationError, match="signature"):
            await self._verify("shared-secret", organization=str(uuid4()))
