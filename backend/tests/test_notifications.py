"""NotificationService provider-independence.

The point of these tests is that business logic never learns which transport is
configured. If a change makes SES leak into a caller, these fail.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services.notifications import (
    EmailAddress,
    EmailMessage,
    EmailProvider,
    NotificationService,
    build_email_provider,
)
from app.services.notifications.console import ConsoleEmailProvider

STRONG_SECRET = "a" * 32


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, JWT_SECRET=STRONG_SECRET, **overrides)  # type: ignore[arg-type]


class TestEmailMessageValidation:
    def test_requires_a_recipient(self) -> None:
        with pytest.raises(ValueError, match="at least one recipient"):
            EmailMessage(to=[], subject="s", html_body="<p>h</p>", text_body="t")

    def test_requires_a_subject(self) -> None:
        with pytest.raises(ValueError, match="subject"):
            EmailMessage(
                to=[EmailAddress("a@b.com")], subject="  ", html_body="<p>h</p>", text_body="t"
            )

    def test_requires_a_text_alternative(self) -> None:
        """HTML-only mail is a spam signal and unreadable in text clients."""
        with pytest.raises(ValueError, match="text_body"):
            EmailMessage(
                to=[EmailAddress("a@b.com")], subject="s", html_body="<p>h</p>", text_body=""
            )


class TestAddressRendering:
    def test_bare_address(self) -> None:
        assert EmailAddress("agent@example.com").render() == "agent@example.com"

    def test_display_name_is_quoted(self) -> None:
        """Unquoted names containing a comma break RFC 5322 parsing."""
        rendered = EmailAddress("a@b.com", "Chen, Avery").render()
        assert rendered == '"Chen, Avery" <a@b.com>'


class TestProviderResolution:
    def test_console_provider_selected_by_config(self) -> None:
        provider = build_email_provider(_settings(EMAIL_PROVIDER="console"))
        assert provider.name == "console"

    def test_adapters_satisfy_the_protocol(self) -> None:
        """Structural typing: an adapter needs no base class."""
        assert isinstance(ConsoleEmailProvider(), EmailProvider)


class TestNotificationService:
    async def test_password_reset_is_transport_agnostic(self) -> None:
        provider = ConsoleEmailProvider()
        service = NotificationService(provider=provider)

        result = await service.send_password_reset(
            to=EmailAddress("agent@example.com", "Avery"),
            reset_url="https://app.example.com/reset?token=abc",
            expires_minutes=30,
        )

        assert result.provider == "console"
        assert len(provider.sent) == 1

        message = provider.sent[0]
        assert "30 minutes" in message.text_body
        # Both alternatives must carry the link, not just the HTML part.
        assert "https://app.example.com/reset?token=abc" in message.text_body
        assert "https://app.example.com/reset?token=abc" in message.html_body

    async def test_invitation_names_the_inviter(self) -> None:
        provider = ConsoleEmailProvider()
        service = NotificationService(provider=provider)

        await service.send_user_invitation(
            to=EmailAddress("new@example.com"),
            inviter_name="Avery Chen",
            accept_url="https://app.example.com/invite/xyz",
        )

        assert "Avery Chen" in provider.sent[0].subject

    async def test_swapping_provider_does_not_change_the_call(self) -> None:
        """The core guarantee: callers are written once, transport is config."""

        class FakeProvider:
            name = "fake"

            def __init__(self) -> None:
                self.sent: list[EmailMessage] = []

            async def send(self, message: EmailMessage):  # type: ignore[no-untyped-def]
                from app.services.notifications.base import SendResult

                self.sent.append(message)
                return SendResult(provider_message_id="fake-1", provider=self.name)

            async def verify_configuration(self) -> bool:
                return True

        fake = FakeProvider()
        result = await NotificationService(provider=fake).send_password_reset(
            to=EmailAddress("a@b.com"), reset_url="https://x/y", expires_minutes=15
        )

        assert result.provider == "fake"
        assert len(fake.sent) == 1
