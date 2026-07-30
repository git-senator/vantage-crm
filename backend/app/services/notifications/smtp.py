"""SMTP adapter — vendor-neutral email over any SMTP server.

The AWS-free transport: Gmail, Resend, a corporate relay, anything that speaks
SMTP. It exists so a self-hosted single-VPS deployment can send real
transactional mail (password resets, invitations) without an AWS SES account.

`smtplib` is synchronous, so a send is dispatched to a worker thread rather than
blocking the event loop — the same reason the SES adapter offloads boto3. The
message is composed with `email.message.EmailMessage` rather than by string
concatenation: MIME boundaries, header folding and non-ASCII encoding are each a
place where hand-rolled output is subtly wrong in a way only some clients reveal.

The password is read once from a `SecretStr` and never logged; recipient
addresses are PII and are not logged either — only counts are.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
import uuid
from email.message import EmailMessage as MimeMessage
from functools import partial

from app.core.config import Settings
from app.core.logging import get_logger
from app.services.notifications.base import (
    EmailMessage,
    NotificationError,
    SendResult,
)

logger = get_logger(__name__)

#: Implicit-TLS port. Any other port is treated as plain-then-STARTTLS.
_IMPLICIT_TLS_PORT = 465


class SMTPEmailProvider:
    name = "smtp"

    def __init__(self, settings: Settings) -> None:
        self._host = settings.SMTP_HOST
        self._port = settings.SMTP_PORT
        self._username = settings.SMTP_USERNAME
        self._password = settings.SMTP_PASSWORD.get_secret_value()
        self._starttls = settings.SMTP_STARTTLS
        self._timeout = settings.SMTP_TIMEOUT_SECONDS
        self._from = settings.EMAIL_FROM
        self._from_name = settings.EMAIL_FROM_NAME

    def _build_mime(self, message: EmailMessage) -> MimeMessage:
        mime = MimeMessage()
        mime["From"] = f'"{self._from_name}" <{self._from}>'
        mime["To"] = ", ".join(addr.render() for addr in message.to)
        if message.cc:
            mime["Cc"] = ", ".join(addr.render() for addr in message.cc)
        if message.reply_to:
            mime["Reply-To"] = message.reply_to.render()
        mime["Subject"] = message.subject

        for name, value in message.headers.items():
            # Assigned, not appended: a duplicate Message-ID is a malformed
            # message, and `mime[name] = ...` on an existing key appends in the
            # stdlib API rather than replacing.
            del mime[name]
            mime[name] = value

        # Text first, then the HTML alternative — order matters: a client picks
        # the last part it can render, so HTML must come second to be preferred.
        mime.set_content(message.text_body)
        mime.add_alternative(message.html_body, subtype="html")
        return mime

    def _envelope_recipients(self, message: EmailMessage) -> list[str]:
        # Bcc travels in the envelope only, never in the MIME headers — putting
        # it in the document would disclose it to every recipient, the one thing
        # Bcc must never do.
        return [
            addr.address
            for addr in (*message.to, *message.cc, *message.bcc)
        ]

    def _send_sync(self, mime: MimeMessage, recipients: list[str]) -> None:
        context = ssl.create_default_context()
        if self._port == _IMPLICIT_TLS_PORT:
            with smtplib.SMTP_SSL(
                self._host, self._port, timeout=self._timeout, context=context
            ) as server:
                if self._username:
                    server.login(self._username, self._password)
                server.send_message(mime, from_addr=self._from, to_addrs=recipients)
            return
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as server:
            server.ehlo()
            if self._starttls:
                server.starttls(context=context)
                server.ehlo()
            if self._username:
                server.login(self._username, self._password)
            server.send_message(mime, from_addr=self._from, to_addrs=recipients)

    async def send(self, message: EmailMessage) -> SendResult:
        if not self._host:
            raise NotificationError("SMTP_HOST is not configured.", retryable=False)

        mime = self._build_mime(message)
        recipients = self._envelope_recipients(message)
        loop = asyncio.get_running_loop()

        try:
            await loop.run_in_executor(
                None, partial(self._send_sync, mime, recipients)
            )
        except smtplib.SMTPAuthenticationError as exc:
            # Wrong credentials will not succeed on retry, and repeated failed
            # logins can lock the account — terminal.
            logger.error("smtp_auth_failed", extra={"host": self._host})
            raise NotificationError(
                "SMTP authentication failed.", retryable=False
            ) from exc
        except smtplib.SMTPRecipientsRefused as exc:
            logger.error(
                "smtp_recipients_refused",
                extra={"recipient_count": len(message.to)},
            )
            raise NotificationError(
                "SMTP recipients were refused.", retryable=False
            ) from exc
        except (smtplib.SMTPException, OSError) as exc:
            # Connection reset, greylisting, a transient 4xx — worth a retry.
            logger.warning("smtp_transport_error", extra={"host": self._host})
            raise NotificationError("SMTP transport failure.", retryable=True) from exc

        message_id = mime.get("Message-ID") or f"smtp-{uuid.uuid4()}"
        logger.info(
            "email_sent",
            extra={
                "provider": self.name,
                "provider_message_id": str(message_id),
                "recipient_count": len(message.to),
            },
        )
        return SendResult(provider_message_id=str(message_id), provider=self.name)

    async def verify_configuration(self) -> bool:
        if not self._host:
            return False
        loop = asyncio.get_running_loop()

        def _check() -> bool:
            context = ssl.create_default_context()
            if self._port == _IMPLICIT_TLS_PORT:
                with smtplib.SMTP_SSL(
                    self._host, self._port, timeout=self._timeout, context=context
                ) as server:
                    if self._username:
                        server.login(self._username, self._password)
            else:
                with smtplib.SMTP(
                    self._host, self._port, timeout=self._timeout
                ) as server:
                    server.ehlo()
                    if self._starttls:
                        server.starttls(context=context)
                        server.ehlo()
                    if self._username:
                        server.login(self._username, self._password)
            return True

        try:
            return await loop.run_in_executor(None, _check)
        except Exception:
            logger.warning("smtp_configuration_invalid", exc_info=True)
            return False


__all__ = ["SMTPEmailProvider"]
