"""Notification package.

Import from here. Nothing outside this package should reference an adapter
module directly — that is what keeps the provider swappable.
"""

from app.services.notifications.base import (
    EmailAddress,
    EmailMessage,
    EmailProvider,
    NotificationError,
    SendResult,
)
from app.services.notifications.service import NotificationService, build_email_provider

__all__ = [
    "EmailAddress",
    "EmailMessage",
    "EmailProvider",
    "NotificationError",
    "NotificationService",
    "SendResult",
    "build_email_provider",
]
