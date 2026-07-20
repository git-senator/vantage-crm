"""NotificationService — the only notification API business logic should touch.

Callers express *intent* ("send a password reset"), not transport. Nothing
outside this package imports SES, boto3, or an adapter type, which is what
makes the provider swappable without touching business code.

Templates live here rather than in adapters so switching provider cannot change
what a customer receives.
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.services.notifications.base import (
    EmailAddress,
    EmailMessage,
    EmailProvider,
    NotificationError,
    SendResult,
)
from app.services.notifications.console import ConsoleEmailProvider
from app.services.notifications.ses import SESEmailProvider

logger = get_logger(__name__)


def build_email_provider(settings: Settings) -> EmailProvider:
    """Resolve the configured adapter.

    Add a provider by writing an adapter and extending this match — no caller
    changes anywhere.
    """
    match settings.EMAIL_PROVIDER:
        case "ses":
            return SESEmailProvider(settings)
        case "console":
            return ConsoleEmailProvider()
        case unknown:  # pragma: no cover — Literal makes this unreachable
            raise ValueError(f"Unsupported EMAIL_PROVIDER: {unknown}")


class NotificationService:
    def __init__(self, provider: EmailProvider | None = None) -> None:
        self._settings = get_settings()
        self._provider = provider or build_email_provider(self._settings)

    @property
    def provider_name(self) -> str:
        return self._provider.name

    async def _send(self, message: EmailMessage) -> SendResult:
        try:
            return await self._provider.send(message)
        except NotificationError:
            raise
        except Exception as exc:  # adapter bug — never crash the caller
            logger.exception("notification_provider_error")
            raise NotificationError(str(exc), retryable=True) from exc

    # ------------------------------------------------------------ intents
    #
    # Phase 1 wires password reset and user invitation. Signatures are settled
    # now so callers written against them do not change later.

    async def send_password_reset(
        self, *, to: EmailAddress, reset_url: str, expires_minutes: int
    ) -> SendResult:
        subject = "Reset your Vantage password"
        text_body = (
            f"Hello {to.name or 'there'},\n\n"
            f"Use the link below to set a new password. It expires in "
            f"{expires_minutes} minutes.\n\n{reset_url}\n\n"
            "If you did not request this, you can ignore this message — your "
            "password will not change.\n"
        )
        html_body = (
            f"<p>Hello {to.name or 'there'},</p>"
            f"<p>Use the link below to set a new password. "
            f"It expires in {expires_minutes} minutes.</p>"
            f'<p><a href="{reset_url}">Reset password</a></p>'
            "<p>If you did not request this, you can ignore this message — "
            "your password will not change.</p>"
        )
        return await self._send(
            EmailMessage(
                to=[to],
                subject=subject,
                html_body=html_body,
                text_body=text_body,
                tags={"category": "password_reset"},
            )
        )

    async def send_user_invitation(
        self, *, to: EmailAddress, inviter_name: str, accept_url: str
    ) -> SendResult:
        subject = f"{inviter_name} invited you to Vantage CRM"
        text_body = (
            f"{inviter_name} has invited you to join their Vantage CRM "
            f"workspace.\n\nAccept the invitation:\n{accept_url}\n"
        )
        html_body = (
            f"<p>{inviter_name} has invited you to join their Vantage CRM "
            f"workspace.</p>"
            f'<p><a href="{accept_url}">Accept invitation</a></p>'
        )
        return await self._send(
            EmailMessage(
                to=[to],
                subject=subject,
                html_body=html_body,
                text_body=text_body,
                tags={"category": "user_invitation"},
            )
        )

    async def verify(self) -> bool:
        return await self._provider.verify_configuration()
