"""Console adapter for local development and tests.

Renders the message to the log instead of sending it, so local development
needs no AWS credentials and a test run can never email a real person — the
single most embarrassing class of staging accident.
"""

from __future__ import annotations

import uuid

from app.core.logging import get_logger
from app.services.notifications.base import EmailMessage, SendResult

logger = get_logger(__name__)


class ConsoleEmailProvider:
    name = "console"

    def __init__(self) -> None:
        # Retained so tests can assert on what would have been sent.
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> SendResult:
        self.sent.append(message)
        message_id = f"console-{uuid.uuid4()}"

        logger.info(
            "email_would_send",
            extra={
                "provider": self.name,
                "provider_message_id": message_id,
                "to": [addr.address for addr in message.to],
                "subject": message.subject,
                "body_preview": message.text_body[:200],
            },
        )
        return SendResult(provider_message_id=message_id, provider=self.name)

    async def verify_configuration(self) -> bool:
        return True
