"""Instagram and Facebook outbound, over Meta's Send API.

Almost nothing here is about "does the HTTP call work". It is about the two
ways this channel lies to you if you let it.

The first is the **handle that is not an address**. Meta never hands over the
counterparty's `@username` — it gives a per-app scoped id. A conversation that
arrived through the orchestrator carrying a handle looks perfectly answerable
in the inbox, and would fail at Meta with a permissions error that reads like a
token problem. `TestRecipientIdentity` pins the refusal, and pins that it is
terminal: retrying a handle produces the same handle.

The second is **error classification**. Meta's numeric subcodes are many and
versioned, so this adapter judges by transport class instead. That is a
deliberate coarseness and it needs a test, because the failure mode of getting
it wrong is invisible: a message retried until the queue gives up, or one
abandoned that a second attempt would have delivered.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.services.messaging import (
    MessagingError,
    OutboundMessage,
    build_channel,
    reset_channels,
)
from app.services.messaging.meta_channel import MetaMessagingChannel

IGSID = "17841400000000000"


def _settings(**overrides) -> Settings:  # type: ignore[no-untyped-def]
    values = {
        "JWT_SECRET": "x" * 32,
        "META_API_BASE": "https://graph.test/v21.0",
        "INSTAGRAM_ACCOUNT_ID": "17841499999999999",
        "INSTAGRAM_ACCESS_TOKEN": "ig-token",
        "FACEBOOK_PAGE_ID": "998877665544",
        "FACEBOOK_ACCESS_TOKEN": "fb-token",
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def _responder(monkeypatch, response: httpx.Response, captured: dict) -> None:  # type: ignore[no-untyped-def]
    class _Client:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url, json, headers):  # type: ignore[no-untyped-def]
            captured.update({"url": url, "json": json, "headers": headers})
            return response

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: _Client())


class TestRecipientIdentity:
    @pytest.mark.parametrize(
        "address", ["@ana_volkova", "ana volkova", "ana@example.com", "12345"]
    )
    def test_a_handle_is_not_an_address(self, address: str) -> None:
        assert MetaMessagingChannel.is_scoped_id(address) is False

    def test_a_scoped_id_is(self) -> None:
        assert MetaMessagingChannel.is_scoped_id(IGSID) is True

    def test_normalisation_never_raises_on_junk(self) -> None:
        channel = MetaMessagingChannel("instagram", _settings())
        assert channel.normalise_address("   ") == ""
        assert channel.normalise_address(f"  {IGSID} ") == IGSID

    async def test_a_handle_is_refused_before_the_api_call(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        captured: dict = {}
        _responder(monkeypatch, httpx.Response(200), captured)

        with pytest.raises(MessagingError) as caught:
            await MetaMessagingChannel("instagram", _settings()).send(
                OutboundMessage(
                    to_address="@ana_volkova", to_name="Ana", body_text="Oi"
                )
            )

        # Terminal: a retry produces the same handle and the same refusal.
        assert caught.value.retryable is False
        # And the reason is one a person can act on, not a Meta code.
        assert "@ana_volkova" in str(caught.value)
        assert not captured, "nothing should have been sent"


class TestConfiguration:
    async def test_an_unconfigured_deployment_refuses_terminally(self) -> None:
        channel = MetaMessagingChannel(
            "instagram", _settings(INSTAGRAM_ACCESS_TOKEN="")
        )
        with pytest.raises(MessagingError) as caught:
            await channel.send(
                OutboundMessage(to_address=IGSID, to_name="Ana", body_text="Oi")
            )
        assert caught.value.retryable is False

    def test_the_two_surfaces_do_not_share_a_token(self) -> None:
        settings = _settings()
        ig = MetaMessagingChannel("instagram", settings)
        fb = MetaMessagingChannel("facebook", settings)
        assert ig._credentials() != fb._credentials()

    def test_build_channel_routes_meta_names_to_this_adapter(self) -> None:
        reset_channels()
        try:
            assert isinstance(build_channel("instagram"), MetaMessagingChannel)
            assert isinstance(build_channel("facebook"), MetaMessagingChannel)
        finally:
            reset_channels()


class TestSending:
    async def test_a_successful_send_returns_metas_message_id(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        captured: dict = {}
        _responder(
            monkeypatch,
            httpx.Response(
                200,
                json={"recipient_id": IGSID, "message_id": "mid.XYZ"},
                request=httpx.Request("POST", "https://graph.test/v21.0/x/messages"),
            ),
            captured,
        )

        result = await MetaMessagingChannel("instagram", _settings()).send(
            OutboundMessage(to_address=IGSID, to_name="Ana", body_text="Oi, tudo bem?")
        )

        assert result.provider_message_id == "mid.XYZ"
        assert result.channel == "instagram"
        # Sent from the Instagram account, not the Page.
        assert captured["url"] == "https://graph.test/v21.0/17841499999999999/messages"
        assert captured["json"]["recipient"]["id"] == IGSID
        assert captured["json"]["message"]["text"] == "Oi, tudo bem?"

    async def test_facebook_sends_from_the_page(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        captured: dict = {}
        _responder(
            monkeypatch,
            httpx.Response(
                200,
                json={"message_id": "mid.FB"},
                request=httpx.Request("POST", "https://graph.test/v21.0/x/messages"),
            ),
            captured,
        )

        result = await MetaMessagingChannel("facebook", _settings()).send(
            OutboundMessage(to_address=IGSID, to_name="Ana", body_text="Olá")
        )

        assert result.channel == "facebook"
        assert captured["url"] == "https://graph.test/v21.0/998877665544/messages"
        assert captured["headers"]["Authorization"] == "Bearer fb-token"

    async def test_an_accepted_message_without_an_id_is_an_error(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        _responder(
            monkeypatch,
            httpx.Response(
                200,
                json={"recipient_id": IGSID},
                request=httpx.Request("POST", "https://graph.test/v21.0/x/messages"),
            ),
            {},
        )
        with pytest.raises(MessagingError):
            await MetaMessagingChannel("instagram", _settings()).send(
                OutboundMessage(to_address=IGSID, to_name="Ana", body_text="Oi")
            )


class TestErrorClassification:
    """Transport class, not subcode. The distinction the queue acts on."""

    @pytest.mark.parametrize(
        ("status", "retryable"),
        [
            (400, False),  # our payload or permissions — a retry repeats it
            (403, False),
            (429, True),  # rate limited: the same call later succeeds
            (500, True),
            (503, True),
        ],
    )
    async def test_status_decides_whether_a_retry_is_worth_it(
        self, monkeypatch, status: int, retryable: bool
    ) -> None:  # type: ignore[no-untyped-def]
        _responder(
            monkeypatch,
            httpx.Response(
                status,
                json={"error": {"message": "nope", "code": 10}},
                request=httpx.Request("POST", "https://graph.test/v21.0/x/messages"),
            ),
            {},
        )
        with pytest.raises(MessagingError) as caught:
            await MetaMessagingChannel("instagram", _settings()).send(
                OutboundMessage(to_address=IGSID, to_name="Ana", body_text="Oi")
            )
        assert caught.value.retryable is retryable

    async def test_metas_own_wording_reaches_the_agent(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """`error_user_msg` is the string Meta wrote for a human; prefer it."""
        _responder(
            monkeypatch,
            httpx.Response(
                400,
                json={
                    "error": {
                        "message": "(#10) Application does not have permission",
                        "error_user_msg": "This person isn't accepting messages.",
                    }
                },
                request=httpx.Request("POST", "https://graph.test/v21.0/x/messages"),
            ),
            {},
        )
        with pytest.raises(MessagingError) as caught:
            await MetaMessagingChannel("instagram", _settings()).send(
                OutboundMessage(to_address=IGSID, to_name="Ana", body_text="Oi")
            )
        assert "isn't accepting messages" in str(caught.value)

    async def test_a_transport_failure_is_retryable(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        class _Client:
            async def __aenter__(self):  # type: ignore[no-untyped-def]
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            async def post(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
                raise httpx.ConnectTimeout("timed out")

        monkeypatch.setattr(httpx, "AsyncClient", lambda **_: _Client())

        with pytest.raises(MessagingError) as caught:
            await MetaMessagingChannel("instagram", _settings()).send(
                OutboundMessage(to_address=IGSID, to_name="Ana", body_text="Oi")
            )
        assert caught.value.retryable is True
