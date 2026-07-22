"""The email channel.

Sends through the existing `EmailProvider` abstraction rather than reaching for
boto3 — the provider swap that Phase 1 bought applies here too, and a CRM
message and a password reset should not travel by different routes.

**A known limitation, stated rather than hidden.** True threading needs
`In-Reply-To` and `References` headers, which require the raw-MIME send API
(`SendRawEmail`), and `EmailProvider.send` speaks the simple API. So a reply
sent from the CRM threads correctly *in the CRM* — the conversation is keyed on
the counterparty's address — but may start a new chain in the recipient's own
mail client. `in_reply_to` is carried through this layer and stored, so closing
the gap is a change to the provider adapter and not to anything above it.
"""

from __future__ import annotations

import re

from app.core.logging import get_logger
from app.services.messaging.base import (
    DeliveryResult,
    MessagingError,
    OutboundMessage,
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

    def __init__(self, provider: EmailProvider | None = None) -> None:
        self._service = NotificationService(provider=provider)

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

        try:
            result = await self._service.send_raw(
                to=EmailAddress(address=message.to_address, name=message.to_name),
                subject=message.subject,
                html_body=message.body_html or _as_html(message.body_text),
                text_body=message.body_text,
                category="crm_message",
            )
        except NotificationError as exc:
            raise MessagingError(str(exc), retryable=exc.retryable) from exc

        return DeliveryResult(
            provider_message_id=result.provider_message_id,
            channel=self.name,
            # SES returns the id it will use in the Message-ID header, so the
            # two are the same value here. Kept as separate fields because a
            # different provider will not have that property.
            rfc_message_id=result.provider_message_id,
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
