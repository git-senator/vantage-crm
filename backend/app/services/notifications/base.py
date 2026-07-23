"""Transport-agnostic notification contract.

Business logic depends on `EmailProvider` — never on SES, boto3, or any other
vendor type. Swapping provider is a new adapter plus one environment variable.

The `Protocol` is structural, so an adapter needs no base class and tests can
pass a plain fake without inheriting anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class EmailAddress:
    address: str
    name: str | None = None

    def render(self) -> str:
        # RFC 5322 display-name form. Quoted because names may contain commas.
        return f'"{self.name}" <{self.address}>' if self.name else self.address


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """A message to send.

    `tags` are provider-neutral key/values used for deliverability analytics
    (SES maps them to message tags). Never put PII in a tag — tags land in
    provider-side metrics and event streams.
    """

    to: list[EmailAddress]
    subject: str
    html_body: str
    text_body: str
    reply_to: EmailAddress | None = None
    cc: list[EmailAddress] = field(default_factory=list)
    bcc: list[EmailAddress] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)
    #: Extra RFC 5322 headers — Message-ID, In-Reply-To, References. Present
    #: because threading cannot be expressed any other way: a reply is a reply
    #: because of its headers, not because of its subject line. An adapter that
    #: cannot set headers must fall back to a plain send rather than dropping
    #: them silently, since a thread that quietly stops threading looks like the
    #: feature was never built.
    headers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.to:
            raise ValueError("EmailMessage requires at least one recipient")
        if not self.subject.strip():
            raise ValueError("EmailMessage requires a subject")
        # A text alternative is mandatory: HTML-only mail is a strong spam
        # signal and is unreadable in text-only clients.
        if not self.text_body.strip():
            raise ValueError("EmailMessage requires a text_body alternative")


@dataclass(frozen=True, slots=True)
class SendResult:
    provider_message_id: str
    provider: str


class NotificationError(Exception):
    """Delivery failed. Callers decide whether to retry."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@runtime_checkable
class EmailProvider(Protocol):
    """Implemented by every transport adapter."""

    name: str

    async def send(self, message: EmailMessage) -> SendResult: ...

    async def verify_configuration(self) -> bool:
        """Cheap credential/config check for the readiness probe."""
        ...
