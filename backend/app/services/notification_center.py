"""The notification centre — one place that decides who gets told what.

Distinct from `app/services/notifications/`, which is the *email transport*.
This is the product concept: an event happens, one or more people should know,
and each of them has said how they want to hear about it.

The shape that matters:

    raise()  ──▶ in-app row (always written)
                 └─▶ email enqueued, if the recipient's preference says so

**The in-app row is written whatever the preference says.** A preference governs
whether something is *surfaced as unread*, not whether it happened — a user who
muted deal notifications and later asks "when was I told about this?" should
still find the answer. Muting sets `read_at` at creation instead of skipping the
row, so the bell stays quiet while the history stays complete.

**Nothing in this module sends anything.** Email is enqueued; a request that
raised a notification is never waiting on a mail provider. That the enqueue is
best-effort is the deliberate trade-off documented in `app/workers/queue.py`.

**There is no "notify everyone who can see this record".** Fan-out by scope
would turn one deal update into forty notifications and make the feature
worthless within a week. Recipients are named explicitly by the caller.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.notification import (
    NOTIFICATION_CATEGORIES,
    Notification,
    NotificationPreference,
)
from app.repositories.notification import (
    NotificationPreferenceRepository,
    NotificationRepository,
)
from app.schemas.common import Cursor
from app.schemas.notification import NotificationPreferenceUpdate
from app.workers.queue import JobName, enqueue

logger = get_logger(__name__)


#: Defaults for a user who has never opened the preference screen.
#:
#: In-app is on everywhere: the bell is cheap and dismissible. Email defaults
#: on only where the recipient is likely *away from the product* and the thing
#: is addressed to them personally — being assigned work, or being mentioned.
#: Defaulting email on for everything is how a CRM ends up in a spam filter,
#: taking its password resets with it.
DEFAULT_PREFERENCES: dict[str, tuple[bool, bool]] = {
    # category: (in_app, email)
    "lead": (True, False),
    "deal": (True, False),
    "task": (True, True),
    "document": (True, False),
    "mention": (True, True),
    # System notices are operational — quarantines, failed uploads. In-app
    # only; an email per infrastructure event trains people to ignore them.
    "system": (True, False),
}


class NotificationCenter:
    """Raising and reading notifications.

    Takes a session and an optional actor rather than an `AuthorizationContext`:
    raising a notification is not an authorized action, it is a consequence of
    one that was already authorized. Reading *is* gated — on being the
    recipient, which the repository enforces.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.notifications = NotificationRepository(session)
        self.preferences = NotificationPreferenceRepository(session)

    # ------------------------------------------------------------- raising

    async def raise_notification(
        self,
        *,
        organization_id: UUID,
        recipient_id: UUID,
        category: str,
        type: str,
        title: str,
        body: str | None = None,
        actor_id: UUID | None = None,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Notification | None:
        """Tell one person about one thing.

        Returns `None` when the recipient is the actor — nobody needs telling
        about their own action, and a product that does it anyway is one people
        learn to ignore.
        """
        if actor_id is not None and actor_id == recipient_id:
            return None
        if category not in NOTIFICATION_CATEGORIES:  # pragma: no cover — defensive
            raise ValueError(f"Unknown notification category: {category}")

        in_app, by_email = await self._resolve_preference(
            organization_id, recipient_id, category
        )

        notification = Notification(
            organization_id=organization_id,
            user_id=recipient_id,
            actor_id=actor_id,
            category=category,
            type=type,
            title=title[:200],
            body=body,
            entity_type=entity_type,
            entity_id=entity_id,
            metadata_=metadata or {},
            # Muted means "do not surface", not "did not happen": the row is
            # written pre-read so the history survives while the bell stays
            # quiet.
            read_at=None if in_app else datetime.now(UTC),
        )
        self.session.add(notification)
        await self.session.flush()

        if by_email:
            await enqueue(
                JobName.DELIVER_NOTIFICATION_EMAIL,
                str(notification.id),
                str(organization_id),
                job_id=f"notify-email:{notification.id}",
            )

        logger.info(
            "notification_raised",
            extra={
                "notification_id": str(notification.id),
                "type": type,
                "in_app": in_app,
                "email": by_email,
            },
        )
        return notification

    async def _resolve_preference(
        self, organization_id: UUID, user_id: UUID, category: str
    ) -> tuple[bool, bool]:
        """This user's choice, or the category default.

        Absent means default rather than off, so adding a category later does
        not silently mute it for everyone who already has preference rows.
        """
        stored = await self.preferences.get_for_category(
            organization_id, user_id, category
        )
        if stored is not None:
            return stored.in_app, stored.email
        return DEFAULT_PREFERENCES.get(category, (True, False))

    # ------------------------------------------------------------- reading

    async def list_for_user(
        self,
        organization_id: UUID,
        user_id: UUID,
        *,
        unread_only: bool = False,
        category: str | None = None,
        limit: int = 30,
        cursor: Cursor | None = None,
    ) -> tuple[list[Notification], bool, int]:
        rows, has_more = await self.notifications.list_page(
            organization_id,
            user_id,
            unread_only=unread_only,
            category=category,
            limit=limit,
            cursor=cursor,
        )
        unread = await self.notifications.unread_count(organization_id, user_id)
        return rows, has_more, unread

    async def unread_count(self, organization_id: UUID, user_id: UUID) -> int:
        return await self.notifications.unread_count(organization_id, user_id)

    # ------------------------------------------------------------- marking

    async def mark_read(
        self, notification_id: UUID, organization_id: UUID, user_id: UUID
    ) -> Notification | None:
        """Idempotent: re-reading something already read keeps the first
        timestamp, because "when did you first see this" is the useful fact."""
        notification = await self.notifications.get_for_user(
            notification_id, organization_id, user_id
        )
        if notification is None:
            return None
        if notification.read_at is None:
            notification.read_at = datetime.now(UTC)
            await self.session.flush()
        return notification

    async def mark_all_read(self, organization_id: UUID, user_id: UUID) -> int:
        return await self.notifications.mark_all_read(
            organization_id, user_id, at=datetime.now(UTC)
        )

    # --------------------------------------------------------- preferences

    async def get_preferences(
        self, organization_id: UUID, user_id: UUID
    ) -> list[NotificationPreference]:
        """Every category, with defaults filled in for the unset ones.

        The screen shows all categories whether or not a row exists, so
        materialising the defaults here keeps that from being the frontend's
        job — and keeps the two definitions of "default" from drifting apart.
        """
        stored = {
            row.category: row
            for row in await self.preferences.list_for_user(organization_id, user_id)
        }
        resolved: list[NotificationPreference] = []
        for category in NOTIFICATION_CATEGORIES:
            if category in stored:
                resolved.append(stored[category])
                continue
            in_app, by_email = DEFAULT_PREFERENCES.get(category, (True, False))
            # Transient — not added to the session. A default is not a choice,
            # and writing rows for people who never opened the screen would make
            # a later change to a default invisible to them.
            resolved.append(
                NotificationPreference(
                    organization_id=organization_id,
                    user_id=user_id,
                    category=category,
                    in_app=in_app,
                    email=by_email,
                )
            )
        return resolved

    async def update_preferences(
        self,
        organization_id: UUID,
        user_id: UUID,
        updates: list[NotificationPreferenceUpdate],
    ) -> list[NotificationPreference]:
        for update in updates:
            existing = await self.preferences.get_for_category(
                organization_id, user_id, update.category
            )
            if existing is None:
                self.session.add(
                    NotificationPreference(
                        organization_id=organization_id,
                        user_id=user_id,
                        category=update.category,
                        in_app=update.in_app,
                        email=update.email,
                    )
                )
            else:
                existing.in_app = update.in_app
                existing.email = update.email
        await self.session.flush()
        return await self.get_preferences(organization_id, user_id)
