"""Activity timeline: reads, manual logging, corrections.

Authorization here is unusual and worth reading. An activity has **no scope
anchor of its own** — its visibility follows the record it hangs off, resolved
through `EntityAccess`. See that module for why.

Two consequences:

  * every entity-scoped read proves the caller can read the parent first;
  * the global feed (`list_feed`) is actor-scoped, because "everything that
    happened anywhere" is not a question any single permission answers.

System-written entries (`stage_change`) are immutable. They are the audit-
adjacent part of the timeline, and a funnel event a user can rewrite is not
evidence of anything.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.permissions import Scope
from app.models.activity import Activity
from app.models.user import User
from app.repositories.activity import ActivityCursor, ActivityRepository
from app.schemas.activity import ActivityCreate, ActivityFilters, ActivityUpdate
from app.services.audit import AuditService, build_diff
from app.services.entity_access import EntityAccess
from app.services.rbac import AuthorizationContext, RbacService

logger = get_logger(__name__)

ENTITY_TYPE = "activity"

#: Written by the system, never by a person. Immutable once recorded.
SYSTEM_TYPES: frozenset[str] = frozenset({"stage_change"})

AUDITED_FIELDS = ("subject", "body", "occurred_at")


def is_system_activity(activity: Activity) -> bool:
    return activity.type in SYSTEM_TYPES


class ActivityService:
    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext | None = None
    ) -> None:
        self.session = session
        self.auth = auth
        self.activities = ActivityRepository(session)
        self.audit = AuditService(session)
        self.rbac = RbacService(session)
        # Available only on the authorized path. `record` is called by other
        # services mid-transaction and needs no context of its own.
        self.access = EntityAccess(session, auth) if auth is not None else None

    def _require_auth(self) -> AuthorizationContext:
        if self.auth is None:  # pragma: no cover - programming error
            raise RuntimeError(
                "ActivityService needs an AuthorizationContext for this operation."
            )
        return self.auth

    # ------------------------------------------------------------- writing
    #
    # `record` is the system path: other services call it inside their own
    # transaction, having already authorized the action that caused it. It
    # deliberately takes no AuthorizationContext and performs no checks.

    async def record(
        self,
        *,
        organization_id: UUID,
        actor_id: UUID | None,
        entity_type: str,
        entity_id: UUID,
        type: str,
        subject: str,
        body: str | None = None,
        occurred_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Activity:
        """Append one timeline entry.

        Flushed, not committed: the caller owns the transaction, so an activity
        cannot survive a rolled-back action that it claims to describe.
        """
        activity = Activity(
            organization_id=organization_id,
            actor_id=actor_id,
            entity_type=entity_type,
            entity_id=entity_id,
            type=type,
            subject=subject,
            body=body,
            metadata_=metadata or {},
        )
        if occurred_at is not None:
            activity.occurred_at = occurred_at

        self.session.add(activity)
        await self.session.flush()
        return activity

    # ------------------------------------------------------------- reading

    async def list_for_entity(
        self,
        *,
        entity_type: str,
        entity_id: UUID,
        limit: int = 50,
        cursor: ActivityCursor | None = None,
    ) -> tuple[list[Activity], bool]:
        """One record's timeline, newest first.

        Gated on being able to read the parent, not on an activity permission:
        if you can see the lead, you can see what happened to it.
        """
        auth = self._require_auth()
        assert self.access is not None
        await self.access.assert_readable(entity_type, entity_id)

        filters = ActivityFilters(entity_type=entity_type, entity_id=entity_id)
        return await self.activities.list_page(
            auth.organization_id, filters=filters, limit=limit, cursor=cursor
        )

    async def list_feed(
        self,
        *,
        filters: ActivityFilters,
        limit: int = 50,
        cursor: ActivityCursor | None = None,
    ) -> tuple[list[Activity], bool]:
        """The cross-entity feed.

        Requires `activities.view`, and its scope decides whose activity is
        visible: OWN means your own log, TEAM your team's, ALL the workspace's.
        Narrower than the per-entity timeline on purpose — a feed is a
        management view, and an agent reading one is reading their own work.

        When the caller names an entity, the parent check applies as well, so
        the feed cannot be used to read a timeline the entity endpoint refuses.
        """
        auth = self._require_auth()
        assert self.access is not None

        scope = auth.require("activities.view")
        actor_ids = await self.rbac.owner_ids_for_scope(auth, scope)

        # Naming an entity narrows to that record — and must satisfy the same
        # parent check the entity timeline applies, so the feed cannot be used
        # to read a timeline the entity endpoint would refuse.
        if filters.entity_id is not None and filters.entity_type is not None:
            await self.access.assert_readable(filters.entity_type, filters.entity_id)

        # Asking for an actor outside your scope is an empty result, not an
        # error — the same shape as any other filter that matches nothing, and
        # it avoids confirming that the user exists.
        if (
            actor_ids is not None
            and filters.actor_id is not None
            and filters.actor_id not in actor_ids
        ):
            return [], False

        return await self.activities.list_page(
            auth.organization_id,
            filters=filters,
            limit=limit,
            cursor=cursor,
            actor_ids=actor_ids,
        )

    async def get_activity(self, activity_id: UUID) -> Activity:
        auth = self._require_auth()
        assert self.access is not None

        activity = await self.activities.get(activity_id, auth.organization_id)
        if activity is None:
            raise NotFoundError("Activity not found.")
        # Same rule as the list: readable if the parent is.
        await self.access.assert_readable(activity.entity_type, activity.entity_id)
        return activity

    # ------------------------------------------------------ manual logging

    async def create_activity(self, payload: ActivityCreate, actor: User) -> Activity:
        """Log something that happened.

        Requires `activities.manage` *and* the ability to read the parent —
        both, because logging against a record you cannot see would let you
        write into someone else's timeline.
        """
        auth = self._require_auth()
        assert self.access is not None

        auth.require("activities.manage")
        await self.access.assert_readable(payload.entity_type, payload.entity_id)

        activity = await self.record(
            organization_id=auth.organization_id,
            actor_id=actor.id,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            type=payload.type,
            subject=payload.subject,
            body=payload.body,
            occurred_at=payload.occurred_at,
        )

        await self.audit.record(
            action=AuditAction.RECORD_CREATED,
            organization_id=auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=activity.id,
            metadata={
                "type": activity.type,
                "on": f"{payload.entity_type}:{payload.entity_id}",
            },
        )
        logger.info("activity_logged", extra={"activity_id": str(activity.id)})
        return activity

    async def update_activity(
        self, activity_id: UUID, payload: ActivityUpdate, actor: User
    ) -> Activity:
        """Correct a logged activity.

        Only the person who logged it, or someone with ALL scope. A colleague
        rewriting your record of a call is not a correction.
        """
        auth = self._require_auth()
        activity = await self.get_activity(activity_id)
        auth.require("activities.manage")

        if is_system_activity(activity):
            raise ConflictError(
                "System-recorded activity cannot be edited."
            )
        self._assert_own_or_all(activity, "edit")

        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            return activity

        before = {field: getattr(activity, field) for field in AUDITED_FIELDS}
        for field, value in updates.items():
            setattr(activity, field, value)
        await self.session.flush()

        diff = build_diff(
            before, {field: getattr(activity, field) for field in AUDITED_FIELDS}
        )
        if diff:
            await self.audit.record(
                action=AuditAction.RECORD_UPDATED,
                organization_id=auth.organization_id,
                actor_id=actor.id,
                actor_email=actor.email,
                entity_type=ENTITY_TYPE,
                entity_id=activity.id,
                metadata={"changes": diff},
            )
        return activity

    async def delete_activity(self, activity_id: UUID, actor: User) -> None:
        """Remove a logged activity.

        A hard delete: `activities` has no `deleted_at`, because a timeline
        that silently retains hidden rows is worse to reason about than one
        that does not. The audit entry outlives it either way.
        """
        auth = self._require_auth()
        activity = await self.get_activity(activity_id)
        auth.require("activities.manage")

        if is_system_activity(activity):
            raise ConflictError("System-recorded activity cannot be deleted.")
        self._assert_own_or_all(activity, "delete")

        snapshot = {
            "type": activity.type,
            "subject": activity.subject,
            "on": f"{activity.entity_type}:{activity.entity_id}",
        }
        await self.session.delete(activity)
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_DELETED,
            organization_id=auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=activity_id,
            metadata=snapshot,
        )

    # ------------------------------------------------------------ helpers

    def _assert_own_or_all(self, activity: Activity, verb: str) -> None:
        auth = self._require_auth()
        scope = auth.scope_for("activities.manage")
        if scope is Scope.ALL:
            return
        if activity.actor_id != auth.user_id:
            from app.core.exceptions import PermissionDeniedError

            raise PermissionDeniedError(
                f"You can only {verb} activities you logged yourself."
            )

    async def list_for_entity_unchecked(
        self, *, organization_id: UUID, entity_type: str, entity_id: UUID, limit: int = 50
    ) -> list[Activity]:
        """Timeline without the parent check.

        For callers that have *already* resolved the entity under RBAC — the
        deal detail endpoint, for instance, which fetched the deal first.
        Named explicitly so its use is a deliberate choice rather than an
        accident.
        """
        filters = ActivityFilters(entity_type=entity_type, entity_id=entity_id)
        rows, _ = await self.activities.list_page(
            organization_id, filters=filters, limit=limit
        )
        return rows

    @staticmethod
    def now() -> datetime:
        return datetime.now(UTC)
