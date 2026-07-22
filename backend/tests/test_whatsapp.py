"""WhatsApp — the adapter, the webhook, and the shared pipeline.

The point of these tests is as much what they *don't* have to cover: threading,
record matching, unread counts and the outbound job are all the email code from
Phase 3.4, unchanged, because `channel` is a column. What is genuinely new is
address normalisation, Meta's webhook shape and its signature scheme, and the
24-hour session window.

`TestSessionWindow` is the one that would be easy to skip and shouldn't be.
Outside 24 hours of a customer's last message WhatsApp rejects free-form text at
the API — a platform rule, not a preference — and an agent needs to learn that
from an error they can read rather than from a message stuck in `queued`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AuthenticationError
from app.models.conversation import Conversation, Message
from app.schemas.client import ClientCreate
from app.schemas.lead import LeadCreate
from app.services.client import ClientService
from app.services.conversation import InboundMessageService
from app.services.lead import LeadService
from app.services.messaging import MessagingError, OutboundMessage
from app.services.messaging.whatsapp_channel import WhatsAppChannel, parse_inbound

APP_SECRET = "meta-app-secret"


def _settings(**overrides) -> Settings:  # type: ignore[no-untyped-def]
    values = {
        "JWT_SECRET": "x" * 32,
        "WHATSAPP_API_BASE": "https://graph.test/v21.0",
        "WHATSAPP_PHONE_NUMBER_ID": "1234567890",
        "WHATSAPP_ACCESS_TOKEN": "token",
        "WHATSAPP_APP_SECRET": APP_SECRET,
        "WHATSAPP_VERIFY_TOKEN": "verify-me",
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def _webhook(body: str = "Is the penthouse still available?", **overrides) -> dict:  # type: ignore[type-arg]
    message = {
        "from": "14155550100",
        "id": overrides.get("provider_id", f"wamid.{uuid4().hex}"),
        "timestamp": "1753200000",
        "type": overrides.get("type", "text"),
        "text": {"body": body},
    }
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "acct",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "15550001111"},
                            "contacts": [
                                {"wa_id": "14155550100", "profile": {"name": "Sana Kaur"}}
                            ],
                            "messages": [message],
                        },
                    }
                ],
            }
        ],
    }


class TestAddressNormalisation:
    """The identity function for a thread, in phone-number form."""

    @pytest.mark.parametrize(
        "raw",
        ["+1 (415) 555-0100", "+14155550100", "1-415-555-0100", " 14155550100 "],
    )
    def test_every_spelling_of_a_number_is_one_conversation(self, raw: str) -> None:
        assert WhatsAppChannel(_settings()).normalise_address(raw) == "14155550100"

    def test_normalisation_never_raises_on_junk(self) -> None:
        """It runs on data that arrived from outside, so it must be total."""
        assert WhatsAppChannel(_settings()).normalise_address("not a number") == ""

    def test_length_bounds_reject_a_non_number(self) -> None:
        channel = WhatsAppChannel(_settings())
        assert channel.looks_like_number("+14155550100")
        assert not channel.looks_like_number("911")
        assert not channel.looks_like_number("1" * 20)


class TestOutboundAdapter:
    async def test_an_unconfigured_deployment_refuses_terminally(self) -> None:
        """The agent should learn it is unavailable from the compose box, not
        from a message stuck in `queued`."""
        channel = WhatsAppChannel(_settings(WHATSAPP_ACCESS_TOKEN=""))
        with pytest.raises(MessagingError) as caught:
            await channel.send(
                OutboundMessage(
                    to_address="+14155550100", to_name=None, body_text="Hello"
                )
            )
        assert caught.value.retryable is False

    async def test_an_invalid_number_is_refused_before_the_api_call(self) -> None:
        channel = WhatsAppChannel(_settings())
        with pytest.raises(MessagingError, match="not a valid phone number"):
            await channel.send(
                OutboundMessage(to_address="911", to_name=None, body_text="Hello")
            )

    async def test_a_successful_send_returns_metas_message_id(
        self, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        captured: dict = {}

        class _Client:
            async def __aenter__(self):  # type: ignore[no-untyped-def]
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            async def post(self, url, json, headers):  # type: ignore[no-untyped-def]
                captured.update({"url": url, "json": json, "headers": headers})
                return httpx.Response(
                    200,
                    json={"messages": [{"id": "wamid.ABC"}]},
                    request=httpx.Request("POST", url),
                )

        monkeypatch.setattr(httpx, "AsyncClient", lambda **_: _Client())

        result = await WhatsAppChannel(_settings()).send(
            OutboundMessage(
                to_address="+1 (415) 555-0100", to_name="Sana", body_text="Hello"
            )
        )

        assert result.provider_message_id == "wamid.ABC"
        assert result.channel == "whatsapp"
        # Normalised before it reaches Meta, so the thread identity and the
        # wire format agree.
        assert captured["json"]["to"] == "14155550100"
        # Link previews are off: they make Meta fetch the target, which leaks
        # that a link was sent and to whom.
        assert captured["json"]["text"]["preview_url"] is False


class TestSessionWindow:
    async def test_outside_the_window_is_terminal_with_a_readable_reason(
        self, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        """Meta's 131047. Retrying cannot help — the window will not reopen
        until the customer writes again — so it must not consume the queue's
        retry budget."""

        class _Client:
            async def __aenter__(self):  # type: ignore[no-untyped-def]
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            async def post(self, url, json, headers):  # type: ignore[no-untyped-def]
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "code": 131047,
                            "message": "Re-engagement message",
                        }
                    },
                    request=httpx.Request("POST", url),
                )

        monkeypatch.setattr(httpx, "AsyncClient", lambda **_: _Client())

        with pytest.raises(MessagingError) as caught:
            await WhatsAppChannel(_settings()).send(
                OutboundMessage(
                    to_address="+14155550100", to_name=None, body_text="Hello"
                )
            )

        assert caught.value.retryable is False
        assert "24 hours" in str(caught.value)

    async def test_a_server_error_is_retryable(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        class _Client:
            async def __aenter__(self):  # type: ignore[no-untyped-def]
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            async def post(self, url, json, headers):  # type: ignore[no-untyped-def]
                return httpx.Response(
                    503, json={"error": {"code": 1}}, request=httpx.Request("POST", url)
                )

        monkeypatch.setattr(httpx, "AsyncClient", lambda **_: _Client())

        with pytest.raises(MessagingError) as caught:
            await WhatsAppChannel(_settings()).send(
                OutboundMessage(
                    to_address="+14155550100", to_name=None, body_text="Hello"
                )
            )
        assert caught.value.retryable is True


class TestWebhookParsing:
    def test_a_text_message_is_translated(self) -> None:
        [message] = parse_inbound(_webhook())
        assert message.channel == "whatsapp"
        assert message.from_address == "14155550100"
        assert message.from_name == "Sana Kaur"
        assert message.body_text == "Is the penthouse still available?"

    def test_meta_batches_and_so_do_we(self) -> None:
        """One webhook may carry several entries, each with several changes,
        each with several messages. That is the API's actual shape."""
        payload = _webhook()
        payload["entry"].append(_webhook("Second")["entry"][0])
        assert len(parse_inbound(payload)) == 2

    def test_status_receipts_produce_no_messages(self) -> None:
        """They carry no body, and treating them as messages would fill a
        thread with empty rows."""
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "metadata": {"display_phone_number": "15550001111"},
                                "statuses": [
                                    {"id": "wamid.X", "status": "delivered"}
                                ],
                            }
                        }
                    ]
                }
            ]
        }
        assert parse_inbound(payload) == []

    def test_unsupported_types_are_skipped_not_faked(self) -> None:
        """Pretending an image is an empty text message is worse than skipping
        it."""
        assert parse_inbound(_webhook(type="image")) == []

    def test_a_malformed_entry_does_not_take_the_batch_with_it(self) -> None:
        """One bad message must not reject the whole delivery and have Meta
        retry the good ones forever."""
        payload = _webhook()
        payload["entry"].insert(0, {"changes": [{"value": None}]})
        assert len(parse_inbound(payload)) == 1

    def test_a_message_with_no_id_is_skipped(self) -> None:
        payload = _webhook()
        payload["entry"][0]["changes"][0]["value"]["messages"][0]["id"] = ""
        assert parse_inbound(payload) == []


class TestWebhookAuthentication:
    async def _verify(self, *, secret: str = APP_SECRET, **overrides):  # type: ignore[no-untyped-def]
        from app.api.v1.whatsapp import verify_meta_signature

        body = overrides.get("body", json.dumps(_webhook()).encode())
        signature = overrides.get(
            "signature",
            "sha256="
            + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest(),
        )

        class _Request:
            headers = (
                {} if signature is None else {"X-Hub-Signature-256": signature}
            )

            async def body(self) -> bytes:
                return overrides.get("received_body", body)

        return await verify_meta_signature(
            _Request(),  # type: ignore[arg-type]
            _settings(WHATSAPP_APP_SECRET=secret),
        )

    async def test_a_valid_signature_returns_the_raw_body(self) -> None:
        """The bytes are returned rather than re-read: the signature covers
        exactly these, and verifying one thing while parsing another is the
        classic way to make a signature check decorative."""
        assert await self._verify()

    async def test_an_unset_secret_refuses_everything(self) -> None:
        with pytest.raises(AuthenticationError, match="not configured"):
            await self._verify(secret="")

    async def test_a_missing_signature_is_refused(self) -> None:
        with pytest.raises(AuthenticationError, match="Missing"):
            await self._verify(signature=None)

    async def test_an_unprefixed_signature_is_refused(self) -> None:
        with pytest.raises(AuthenticationError, match="Missing"):
            await self._verify(signature="deadbeef")

    async def test_a_tampered_body_is_refused(self) -> None:
        with pytest.raises(AuthenticationError, match="signature"):
            await self._verify(received_body=b'{"entry":[]}')


class TestSubscriptionHandshake:
    async def _verify(self, **overrides):  # type: ignore[no-untyped-def]
        from app.api.v1.whatsapp import verify_subscription

        return await verify_subscription(
            _settings(),
            hub_mode=overrides.get("mode", "subscribe"),
            hub_challenge=overrides.get("challenge", "1158201444"),
            hub_verify_token=overrides.get("token", "verify-me"),
        )

    async def test_the_challenge_comes_back_as_bare_text(self) -> None:
        """Meta compares the body byte for byte; a JSON-quoted string fails."""
        response = await self._verify()
        assert response.body == b"1158201444"
        assert response.media_type == "text/plain"

    async def test_a_wrong_token_is_refused(self) -> None:
        with pytest.raises(AuthenticationError, match="verify token"):
            await self._verify(token="guess")

    async def test_a_non_subscribe_mode_is_refused(self) -> None:
        with pytest.raises(AuthenticationError, match="subscription request"):
            await self._verify(mode="unsubscribe")


class TestSharedPipeline:
    """The payoff: ingestion is the email code, unchanged."""

    pytestmark = pytest.mark.integration

    async def test_a_whatsapp_message_threads_and_matches_by_phone(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """A CRM phone column holds every spelling a human has used, so the
        stored value is digit-stripped for the comparison."""
        user, auth = admin
        lead = await LeadService(db, auth).create_lead(
            LeadCreate(
                first_name="Sana", last_name="Kaur", phone="+1 (415) 555-0100"
            ),
            user,
        )

        [inbound] = parse_inbound(_webhook())
        await InboundMessageService(db, organization.id).ingest_channel_message(
            inbound
        )

        conversation = (
            (await db.execute(select(Conversation))).unique().scalar_one()
        )
        assert conversation.channel == "whatsapp"
        assert conversation.external_id == "14155550100"
        assert conversation.entity_type == "lead"
        assert conversation.entity_id == lead.id
        assert conversation.unread_count == 1

    async def test_a_replay_does_not_duplicate(
        self, db: AsyncSession, organization
    ) -> None:  # type: ignore[no-untyped-def]
        """Meta signs no timestamp, so dedupe on the provider id is the only
        replay protection there is."""
        [inbound] = parse_inbound(_webhook())
        service = InboundMessageService(db, organization.id)

        _first, created_first = await service.ingest_channel_message(inbound)
        _second, created_second = await service.ingest_channel_message(inbound)

        assert (created_first, created_second) == (True, False)
        assert len((await db.execute(select(Message))).unique().scalars().all()) == 1

    async def test_a_client_matches_by_phone_too(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Any spelling matches, as long as the country code is there."""
        user, auth = admin
        client = await ClientService(db, auth).create_client(
            ClientCreate(
                first_name="Omar", last_name="Haddad", phone="+1 (415) 555-0100"
            ),
            user,
        )

        [inbound] = parse_inbound(_webhook())
        await InboundMessageService(db, organization.id).ingest_channel_message(
            inbound
        )

        conversation = (
            (await db.execute(select(Conversation))).unique().scalar_one()
        )
        assert conversation.entity_type == "client"
        assert conversation.entity_id == client.id

    async def test_a_number_stored_without_a_country_code_does_not_match(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """A known and deliberate limitation, pinned so it stays visible.

        WhatsApp addresses are E.164; `415-555-0100` is ten digits and
        `14155550100` is eleven, so they are not equal and the message lands
        unfiled. The alternative — matching on a trailing-digit suffix — files
        a Colombian number onto a US contact often enough to be worse than an
        unfiled thread, and unfiled is visible and fixable by hand.
        """
        user, auth = admin
        await ClientService(db, auth).create_client(
            ClientCreate(first_name="Omar", last_name="Haddad", phone="415-555-0100"),
            user,
        )

        [inbound] = parse_inbound(_webhook())
        await InboundMessageService(db, organization.id).ingest_channel_message(
            inbound
        )

        conversation = (
            (await db.execute(select(Conversation))).unique().scalar_one()
        )
        assert conversation.entity_type is None

    async def test_email_and_whatsapp_from_one_person_are_separate_threads(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """A conversation is keyed by (channel, address). Merging channels into
        one thread would interleave two different media in one transcript."""
        user, auth = admin
        await LeadService(db, auth).create_lead(
            LeadCreate(
                first_name="Sana",
                last_name="Kaur",
                email="sana@example.com",
                phone="+14155550100",
            ),
            user,
        )
        service = InboundMessageService(db, organization.id)

        [whatsapp_message] = parse_inbound(_webhook())
        await service.ingest_channel_message(whatsapp_message)

        from app.schemas.conversation import InboundEmailPayload

        await service.ingest_email(
            InboundEmailPayload(
                from_address="sana@example.com",
                to_address="inbox@vantagerealty.example",
                body_text="Also emailing.",
                provider_message_id=f"mail-{uuid4()}",
            )
        )

        conversations = (
            (await db.execute(select(Conversation))).unique().scalars().all()
        )
        assert {row.channel for row in conversations} == {"whatsapp", "email"}
        # Both filed against the same lead, which is what makes the record page
        # able to show them together while the transcripts stay distinct.
        assert len({row.entity_id for row in conversations}) == 1
