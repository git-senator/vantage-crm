"""The email channel.

Sends through the existing `EmailProvider` abstraction rather than reaching for
boto3 — the provider swap that Phase 1 bought applies here too, and a CRM
message and a password reset should not travel by different routes.

**Threading is real as of Phase 5.6.** The message carries its own
`Message-ID`, generated here *before* the send and returned so the caller can
record it — a reply must reference an id that already exists, and a
provider-assigned id is unknowable at compose time. `In-Reply-To` and
`References` come from the parent message, and the SES adapter switches to raw
MIME when headers are present because the simple API has no way to carry them.

The Message-ID is minted under the sending domain: a mismatched id is a weak
DMARC signal and there is no reason to spend that credibility. See
`app/services/messaging/threading.py`.
"""

from __future__ import annotations

import re

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.services.messaging.base import (
    DeliveryResult,
    MessagingError,
    OutboundMessage,
)
from app.services.messaging.threading import (
    domain_of,
    generate_message_id,
    threading_headers,
)
from app.services.notifications.base import (
    EmailAddress,
    EmailProvider,
    NotificationError,
)
from app.services.notifications.service import NotificationService

logger = get_logger(__name__)

#: Deliberately permissive. This is not validation — the provider validates —
#: it is a guard against storing something that is obviously not an address as
#: a conversation's identity.
_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EmailChannel:
    name = "email"

    def __init__(
        self, provider: EmailProvider | None = None, settings: Settings | None = None
    ) -> None:
        self._service = NotificationService(provider=provider)
        self._settings = settings

    @property
    def settings(self) -> Settings:
        """Resolved on first send, not in `__init__`.

        Constructing a channel must not require a configured environment: the
        adapter cache builds one at import-adjacent times, and a test that
        passes a fake provider is not asking for the process's settings.
        """
        if self._settings is None:
            self._settings = get_settings()
        return self._settings

    def normalise_address(self, address: str) -> str:
        """Lowercase and strip any display name.

        `"Sana Kaur" <Sana@Example.com>` and `sana@example.com` are the same
        thread, and treating them as two is how an inbox ends up showing the
        same person three times.

        The local part is lowercased too. It is technically case-sensitive per
        RFC 5321, and in practice no mail system anyone uses treats it that way
        — matching the RFC here would split real conversations to satisfy a
        rule the world does not follow.
        """
        candidate = address.strip()
        if "<" in candidate and ">" in candidate:
            candidate = candidate[candidate.rindex("<") + 1 : candidate.rindex(">")]
        return candidate.strip().lower()

    @staticmethod
    def looks_like_address(address: str) -> bool:
        return bool(_ADDRESS.match(address))

    async def send(self, message: OutboundMessage) -> DeliveryResult:
        if not message.subject:
            # A subjectless email is a deliverability problem, not a style one.
            raise MessagingError("Email requires a subject.", retryable=False)

        # Ours, not the provider's. The caller may have generated it already so
        # that it could be recorded before the send — which is what lets a reply
        # to *this* message reference it — and this is the fallback for callers
        # that did not.
        message_id = message.message_id or generate_message_id(
            domain_of(self.settings.EMAIL_FROM)
        )
        headers = threading_headers(
            message_id=message_id,
            in_reply_to=message.in_reply_to,
            references=message.references,
        )

        try:
            result = await self._service.send_raw(
                to=EmailAddress(address=message.to_address, name=message.to_name),
                subject=message.subject,
                html_body=message.body_html or _as_html(message.body_text),
                text_body=message.body_text,
                category="crm_message",
                headers=headers,
            )
        except NotificationError as exc:
            raise MessagingError(str(exc), retryable=exc.retryable) from exc

        return DeliveryResult(
            provider_message_id=result.provider_message_id,
            channel=self.name,
            # The id *we* set, not the provider's. They used to be the same
            # value because SES assigned both; now that the header is ours, the
            # provider id is only useful for looking the send up in SES, and
            # conflating them would put an unthreadable id in the reply chain.
            rfc_message_id=message_id,
        )


def _as_html(text: str) -> str:
    """A minimal HTML alternative for a plain-text message.

    Escaped, not rendered: the body was typed by a user and may legitimately
    contain `<` or `&`. Building the HTML part by interpolating raw text is the
    obvious way to put a stored-XSS hole into a mail client.
    """
    escaped = (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    paragraphs = [line for line in escaped.split("\n\n") if line.strip()]
    return "".join(
        f"<p>{paragraph.replace(chr(10), '<br>')}</p>" for paragraph in paragraphs
    )
