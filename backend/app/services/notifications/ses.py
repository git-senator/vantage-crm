"""Amazon SES adapter.

boto3 is synchronous, so calls are dispatched to a worker thread rather than
blocking the event loop — a blocking network call inside an async handler stalls
every concurrent request on that worker.

SES error taxonomy is mapped to retryable vs. terminal so the queue does not
burn retries on a permanently rejected address, and does retry a throttle.
"""

from __future__ import annotations

import asyncio
from email.message import EmailMessage as MimeMessage
from functools import partial
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings
from app.core.logging import get_logger
from app.services.notifications.base import (
    EmailMessage,
    NotificationError,
    SendResult,
)

logger = get_logger(__name__)

# Terminal: retrying cannot succeed and repeated attempts damage sender
# reputation, which is itself a deliverability risk.
_TERMINAL_ERRORS = frozenset(
    {
        "MessageRejected",
        "MailFromDomainNotVerifiedException",
        "ConfigurationSetDoesNotExistException",
        "AccountSendingPausedException",
    }
)


class SESEmailProvider:
    name = "ses"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any = boto3.client(
            "sesv2",
            region_name=settings.AWS_SES_REGION,
            aws_access_key_id=settings.AWS_SES_ACCESS_KEY_ID.get_secret_value() or None,
            aws_secret_access_key=(
                settings.AWS_SES_SECRET_ACCESS_KEY.get_secret_value() or None
            ),
            config=BotoConfig(
                retries={"max_attempts": 3, "mode": "adaptive"},
                connect_timeout=5,
                read_timeout=10,
            ),
        )

    def _build_request(self, message: EmailMessage) -> dict[str, Any]:
        # Two content shapes, chosen by whether the message needs headers SES's
        # simple API cannot express. The simple path stays the default because
        # it is the one SES validates, formats and reports on most richly; raw
        # MIME is used only when threading demands it.
        if message.headers:
            return self._build_raw_request(message)
        return self._build_simple_request(message)

    def _build_raw_request(self, message: EmailMessage) -> dict[str, Any]:
        """A full MIME document, for messages carrying custom headers.

        SES's `Simple` content has no header field at all, so Message-ID,
        In-Reply-To and References can only travel this way. Composed with
        `email.message.EmailMessage` rather than by string concatenation: MIME
        boundary generation, header folding and non-ASCII encoding are all
        places where hand-rolled output is subtly wrong in a way that only some
        clients reveal.
        """
        settings = self._settings
        mime = MimeMessage()
        mime["From"] = f'"{settings.EMAIL_FROM_NAME}" <{settings.EMAIL_FROM}>'
        mime["To"] = ", ".join(addr.render() for addr in message.to)
        if message.cc:
            mime["Cc"] = ", ".join(addr.render() for addr in message.cc)
        if message.reply_to:
            mime["Reply-To"] = message.reply_to.render()
        mime["Subject"] = message.subject

        for name, value in message.headers.items():
            # Assigned rather than appended: a duplicate Message-ID is a
            # malformed message, and `mime[name] = ...` on an existing key
            # appends a second one in the stdlib API.
            del mime[name]
            mime[name] = value

        mime.set_content(message.text_body)
        mime.add_alternative(message.html_body, subtype="html")

        request: dict[str, Any] = {
            "FromEmailAddress": (
                f'"{settings.EMAIL_FROM_NAME}" <{settings.EMAIL_FROM}>'
            ),
            "Destination": {"ToAddresses": [addr.render() for addr in message.to]},
            "Content": {"Raw": {"Data": mime.as_bytes()}},
        }
        if message.bcc:
            # Bcc stays out of the MIME document by definition and is carried in
            # the envelope instead — putting it in the headers would disclose it
            # to every recipient, which is the one thing Bcc must never do.
            request["Destination"]["BccAddresses"] = [a.render() for a in message.bcc]
        if settings.AWS_SES_CONFIGURATION_SET:
            request["ConfigurationSetName"] = settings.AWS_SES_CONFIGURATION_SET
        if message.tags:
            request["EmailTags"] = [
                {"Name": key, "Value": value} for key, value in message.tags.items()
            ]
        return request

    def _build_simple_request(self, message: EmailMessage) -> dict[str, Any]:
        settings = self._settings
        request: dict[str, Any] = {
            "FromEmailAddress": (
                f'"{settings.EMAIL_FROM_NAME}" <{settings.EMAIL_FROM}>'
            ),
            "Destination": {
                "ToAddresses": [addr.render() for addr in message.to],
            },
            "Content": {
                "Simple": {
                    "Subject": {"Data": message.subject, "Charset": "UTF-8"},
                    "Body": {
                        "Html": {"Data": message.html_body, "Charset": "UTF-8"},
                        "Text": {"Data": message.text_body, "Charset": "UTF-8"},
                    },
                }
            },
        }
        if message.cc:
            request["Destination"]["CcAddresses"] = [a.render() for a in message.cc]
        if message.bcc:
            request["Destination"]["BccAddresses"] = [a.render() for a in message.bcc]
        if message.reply_to:
            request["ReplyToAddresses"] = [message.reply_to.render()]
        if settings.AWS_SES_CONFIGURATION_SET:
            request["ConfigurationSetName"] = settings.AWS_SES_CONFIGURATION_SET
        if message.tags:
            request["EmailTags"] = [
                {"Name": key, "Value": value} for key, value in message.tags.items()
            ]
        return request

    async def send(self, message: EmailMessage) -> SendResult:
        request = self._build_request(message)
        loop = asyncio.get_running_loop()

        try:
            response = await loop.run_in_executor(
                None, partial(self._client.send_email, **request)
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            retryable = code not in _TERMINAL_ERRORS
            # Recipient addresses are PII and are not logged.
            logger.error(
                "ses_send_failed",
                extra={
                    "error_code": code,
                    "retryable": retryable,
                    "recipient_count": len(message.to),
                },
            )
            raise NotificationError(
                f"SES rejected the message: {code}", retryable=retryable
            ) from exc
        except BotoCoreError as exc:
            logger.exception("ses_transport_error")
            raise NotificationError("SES transport failure", retryable=True) from exc

        message_id = str(response.get("MessageId", ""))
        logger.info(
            "email_sent",
            extra={
                "provider": self.name,
                "provider_message_id": message_id,
                "recipient_count": len(message.to),
            },
        )
        return SendResult(provider_message_id=message_id, provider=self.name)

    async def verify_configuration(self) -> bool:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._client.get_account)
            return True
        except (ClientError, BotoCoreError):
            logger.exception("ses_configuration_invalid")
            return False
