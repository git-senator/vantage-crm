"""The operator's view: system health, queue, storage, delivery, audit analytics.

**Read-only, and deliberately so.** There is no "retry this job", no "resend that
email", no "rescan this file" — the same reasoning `JobMonitorService` already
states: the work in this system is re-derivable from database state, the sweeps
re-find it on the next tick, and a manual trigger adds a way for an admin to
re-run something at an arbitrary moment without the idempotency guarantees that
would make it safe.

**Every panel answers a question an operator actually asks at 3am**, and each
one is phrased so the *absence* of a problem is visible. "0 failures" and "the
snapshot job has not run since Tuesday" are different states, and a dashboard
that renders both as green is worse than no dashboard.

Gated on `settings.manage`. Tenant-scoped like everything else: an operator sees
their own workspace's queue, storage and delivery. Infrastructure rows with no
tenant (`job_failures.organization_id IS NULL`) are visible to any admin, which
is the existing `operational_policy_statements` rule — they contain no customer
data by construction and they are the failures most worth seeing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.redis import check_redis
from app.db.session import check_database
from app.models.analytics import MetricSnapshot
from app.models.attachment import Attachment
from app.models.audit import AuditLog
from app.models.conversation import Message
from app.models.job import JobFailure
from app.models.notification import Notification
from app.models.report import ReportRun
from app.services.rbac import AuthorizationContext
from app.services.storage import get_object_storage
from app.workers.queue import get_queue

logger = get_logger(__name__)

#: The window every rate on the admin dashboard is measured over. A day, because
#: a delivery failure rate over the last hour is noise on a workspace that sends
#: forty emails a day, and one over the last month hides an outage that started
#: this morning.
DEFAULT_WINDOW_HOURS = 24

#: How stale the nightly snapshot may be before it is called out. Just over a
#: day: the job runs at 00:07, so anything past ~26 hours means a run was missed
#: rather than merely being early in the cycle.
SNAPSHOT_STALE_HOURS = 26


class AdminService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth

    # ------------------------------------------------------------ health

    async def system_health(self) -> dict[str, Any]:
        """Dependency reachability plus the things readiness cannot see.

        `/health/ready` answers "should this instance take traffic". This
        answers "is the system actually doing its work" — a process can be
        perfectly ready while no worker has run a job since Sunday, and that is
        the outage nobody gets paged for.
        """
        self.auth.require("settings.manage")

        database = await check_database()
        redis = await check_redis()
        try:
            storage = await get_object_storage().verify_configuration()
        except Exception:
            logger.warning("admin_storage_check_failed", exc_info=True)
            storage = False

        queue = await self.queue_health()
        snapshot = await self._snapshot_freshness()

        components = {
            "database": database,
            "redis": redis,
            "storage": storage,
            "queue": queue["reachable"],
            "analytics_snapshot": snapshot["fresh"],
        }
        return {
            # `degraded`, not `down`: the API is answering this request, so the
            # honest report is that something is wrong, not that nothing works.
            "status": "ok" if all(components.values()) else "degraded",
            "components": components,
            "checked_at": datetime.now(UTC),
            "snapshot": snapshot,
        }

    async def _snapshot_freshness(self) -> dict[str, Any]:
        """When analytics last recorded a day.

        A silently dead nightly job is invisible everywhere else: the dashboards
        keep working because today is computed live, and only the history slowly
        stops growing. This is the one place it shows.
        """
        result = await self.session.execute(
            select(func.max(MetricSnapshot.snapshot_date)).where(
                MetricSnapshot.organization_id == self.auth.organization_id
            )
        )
        last = result.scalar()
        if last is None:
            # No snapshot at all is not a failure in a workspace created this
            # morning. Reported as its own state rather than as staleness.
            return {"last_snapshot_date": None, "fresh": True, "reason": "no history yet"}

        age_hours = (datetime.now(UTC).date() - last).days * 24
        return {
            "last_snapshot_date": last,
            "fresh": age_hours <= SNAPSHOT_STALE_HOURS,
            "reason": None if age_hours <= SNAPSHOT_STALE_HOURS else "snapshot job is behind",
        }

    # ------------------------------------------------------------- queue

    async def queue_health(self) -> dict[str, Any]:
        """Depth, workers and dead letters.

        Queue depth is global — ARQ's queue is one sorted set and partitions by
        nothing — while failures are tenant-scoped. Both are reported because
        "nothing is being processed at all" is a failure no per-tenant number
        would reveal.
        """
        self.auth.require("settings.manage")

        queued: int | None = None
        workers: int | None = None
        reachable = True
        try:
            queue = await get_queue()
            queued = int(await queue.zcard(queue.default_queue_name))
            # ARQ writes a health key per worker on a heartbeat. Counting the
            # keys is how "the queue is fine but nothing is draining it" becomes
            # visible — a deep queue with zero workers is a different incident
            # from a deep queue with four.
            workers = len(await queue.keys("arq:health-check*"))
        except Exception:
            logger.warning("admin_queue_check_failed", exc_info=True)
            reachable = False

        open_failures = await self._count(
            select(func.count())
            .select_from(JobFailure)
            .where(self._job_visible())
            .where(JobFailure.resolved_at.is_(None))
        )

        return {
            "reachable": reachable,
            "queued_jobs": queued,
            "workers_seen": workers,
            "open_failures": open_failures,
        }

    def _job_visible(self) -> Any:
        """Tenant rows plus tenant-less infrastructure rows.

        Mirrors `operational_policy_statements`. Stated here as well as in the
        policy: the policy is the boundary, this is the intent, and a reader
        should not have to go and check which is which.
        """
        return or_(
            JobFailure.organization_id == self.auth.organization_id,
            JobFailure.organization_id.is_(None),
        )

    async def job_history(self, *, hours: int = DEFAULT_WINDOW_HOURS) -> list[dict[str, Any]]:
        """Failures by job name over the window, worst first.

        Grouped rather than listed: an operator opening this wants to know
        *which* job is broken, and forty rows of the same dead letter answers
        that more slowly than one row with a count of forty.
        """
        self.auth.require("settings.manage")
        since = datetime.now(UTC) - timedelta(hours=hours)

        query = (
            select(
                JobFailure.job_name,
                func.count().label("failures"),
                func.sum(JobFailure.attempts).label("attempts"),
                func.count().filter(JobFailure.resolved_at.is_(None)).label("unresolved"),
                func.max(JobFailure.last_failed_at).label("last_failed_at"),
            )
            .select_from(JobFailure)
            .where(self._job_visible())
            .where(JobFailure.last_failed_at >= since)
            .group_by(JobFailure.job_name)
            .order_by(func.count().desc())
        )
        rows = (await self.session.execute(query)).all()
        return [
            {
                "job_name": name,
                "failures": int(failures or 0),
                "attempts": int(attempts or 0),
                "unresolved": int(unresolved or 0),
                "last_failed_at": last,
            }
            for name, failures, attempts, unresolved, last in rows
        ]

    # ----------------------------------------------------------- storage

    async def storage_usage(self) -> dict[str, Any]:
        """What this tenant is holding, and what is stuck.

        `pending` and `quarantined` are broken out because they are the two
        states that cost money without delivering value — an abandoned upload
        occupies a row and possibly bytes nobody will ever fetch, and a
        quarantined file is one somebody is probably waiting on.
        """
        self.auth.require("settings.manage")
        organization = self.auth.organization_id

        query = (
            select(
                func.count().label("total"),
                func.coalesce(func.sum(Attachment.size_bytes), 0).label("bytes"),
                func.count().filter(Attachment.status == "available").label("available"),
                func.count().filter(Attachment.status == "pending_upload").label("pending"),
                func.count().filter(Attachment.scan_status == "infected").label("quarantined"),
                func.count().filter(Attachment.scan_status == "pending").label("unscanned"),
            )
            .select_from(Attachment)
            .where(Attachment.organization_id == organization)
            .where(Attachment.deleted_at.is_(None))
        )
        row = (await self.session.execute(query)).one()

        exports = await self._count(
            select(func.count())
            .select_from(ReportRun)
            .where(ReportRun.organization_id == organization)
            .where(ReportRun.storage_key.is_not(None))
        )
        export_bytes = await self._count(
            select(func.coalesce(func.sum(ReportRun.size_bytes), 0))
            .select_from(ReportRun)
            .where(ReportRun.organization_id == organization)
            .where(ReportRun.storage_key.is_not(None))
        )

        return {
            "attachments": int(row[0] or 0),
            "attachment_bytes": int(row[1] or 0),
            "available": int(row[2] or 0),
            "pending_upload": int(row[3] or 0),
            "quarantined": int(row[4] or 0),
            "unscanned": int(row[5] or 0),
            "export_files": exports,
            "export_bytes": export_bytes,
        }

    # ---------------------------------------------------------- delivery

    async def email_delivery(self, *, hours: int = DEFAULT_WINDOW_HOURS) -> dict[str, Any]:
        """Outbound email outcomes over the window.

        Inbound is excluded: a received message has no delivery outcome, and
        folding it in would dilute the failure rate that this panel exists to
        surface.
        """
        self.auth.require("settings.manage")
        since = datetime.now(UTC) - timedelta(hours=hours)

        query = (
            select(
                func.count().label("total"),
                func.count().filter(Message.status == "sent").label("sent"),
                func.count().filter(Message.status == "failed").label("failed"),
                func.count().filter(Message.status == "queued").label("queued"),
            )
            .select_from(Message)
            .where(Message.organization_id == self.auth.organization_id)
            .where(Message.direction == "outbound")
            .where(Message.created_at >= since)
        )
        row = (await self.session.execute(query)).one()
        total, sent, failed, queued = (int(v or 0) for v in row)

        recent = await self._recent_failures(since)

        return {
            "window_hours": hours,
            "total": total,
            "sent": sent,
            "failed": failed,
            "queued": queued,
            # None rather than 0% when nothing was sent. A failure rate over no
            # messages is undefined, and rendering 0% reads as "all good" on a
            # workspace whose email integration is switched off entirely.
            "failure_rate": round(failed / total * 100, 1) if total else None,
            "recent_failures": recent,
        }

    async def _recent_failures(self, since: datetime) -> list[dict[str, Any]]:
        """Why sends failed, grouped by reason.

        The reason, not the message: an operator needs to know whether this is
        one bounced address or the whole SES account being throttled, and a list
        of forty recipients does not distinguish those.
        """
        query = (
            select(
                func.coalesce(Message.failure_reason, "Unknown"),
                func.count(),
            )
            .select_from(Message)
            .where(Message.organization_id == self.auth.organization_id)
            .where(Message.direction == "outbound")
            .where(Message.status == "failed")
            .where(Message.created_at >= since)
            .group_by(Message.failure_reason)
            .order_by(func.count().desc())
            .limit(10)
        )
        rows = (await self.session.execute(query)).all()
        return [{"reason": str(reason), "count": int(count)} for reason, count in rows]

    async def notification_delivery(self, *, hours: int = DEFAULT_WINDOW_HOURS) -> dict[str, Any]:
        """In-app and email notification volumes, and how many landed.

        `unread` is a product signal as much as an operational one: a workspace
        where nothing is ever read is one where notifications have become noise,
        and that is worth seeing next to the delivery numbers.
        """
        self.auth.require("settings.manage")
        since = datetime.now(UTC) - timedelta(hours=hours)

        query = (
            select(
                func.count().label("total"),
                func.count().filter(Notification.read_at.is_(None)).label("unread"),
                func.count().filter(Notification.emailed_at.is_not(None)).label("emailed"),
            )
            .select_from(Notification)
            .where(Notification.organization_id == self.auth.organization_id)
            .where(Notification.created_at >= since)
        )
        row = (await self.session.execute(query)).one()

        by_category = (
            await self.session.execute(
                select(Notification.category, func.count())
                .select_from(Notification)
                .where(Notification.organization_id == self.auth.organization_id)
                .where(Notification.created_at >= since)
                .group_by(Notification.category)
                .order_by(func.count().desc())
            )
        ).all()

        return {
            "window_hours": hours,
            "total": int(row[0] or 0),
            "unread": int(row[1] or 0),
            "emailed": int(row[2] or 0),
            "by_category": [
                {"category": str(category), "count": int(count)} for category, count in by_category
            ],
        }

    # ---------------------------------------------------- audit analytics

    async def audit_analytics(self, *, hours: int = DEFAULT_WINDOW_HOURS) -> dict[str, Any]:
        """What happened in the workspace, and what was refused.

        `denied` is the number worth alerting on. A handful is somebody
        exploring the UI; a sustained stream from one actor against one
        permission is either a misconfigured role or somebody probing, and
        neither is visible in a total event count.
        """
        self.auth.require("settings.manage")
        since = datetime.now(UTC) - timedelta(hours=hours)
        organization = self.auth.organization_id

        base = (AuditLog.organization_id == organization, AuditLog.created_at >= since)

        total = await self._count(select(func.count()).select_from(AuditLog).where(and_(*base)))

        by_action = (
            await self.session.execute(
                select(AuditLog.action, func.count())
                .select_from(AuditLog)
                .where(and_(*base))
                .group_by(AuditLog.action)
                .order_by(func.count().desc())
                .limit(15)
            )
        ).all()

        by_actor = (
            await self.session.execute(
                select(
                    AuditLog.actor_id,
                    func.coalesce(AuditLog.actor_email, "system"),
                    func.count(),
                )
                .select_from(AuditLog)
                .where(and_(*base))
                .group_by(AuditLog.actor_id, AuditLog.actor_email)
                .order_by(func.count().desc())
                .limit(10)
            )
        ).all()

        denied = await self._count(
            select(func.count())
            .select_from(AuditLog)
            .where(and_(*base))
            .where(AuditLog.action == "authz.denied")
        )
        exported = await self._count(
            select(func.count())
            .select_from(AuditLog)
            .where(and_(*base))
            .where(AuditLog.action == "record.exported")
        )

        return {
            "window_hours": hours,
            "total_events": total,
            "denied": denied,
            "exports": exported,
            "by_action": [
                {"action": str(action), "count": int(count)} for action, count in by_action
            ],
            "by_actor": [
                {
                    "actor_id": actor_id,
                    "actor_email": str(email),
                    "count": int(count),
                }
                for actor_id, email, count in by_actor
            ],
        }

    # -------------------------------------------------------- assembled

    async def overview(self, *, hours: int = DEFAULT_WINDOW_HOURS) -> dict[str, Any]:
        """The whole admin dashboard, in one round trip.

        Assembled here for the same reason the analytics dashboards are: seven
        calls to build one screen re-authorise seven times and can render panels
        captured at different moments.
        """
        self.auth.require("settings.manage")
        return {
            "health": await self.system_health(),
            "queue": await self.queue_health(),
            "jobs": await self.job_history(hours=hours),
            "storage": await self.storage_usage(),
            "email": await self.email_delivery(hours=hours),
            "notifications": await self.notification_delivery(hours=hours),
            "audit": await self.audit_analytics(hours=hours),
        }

    # ------------------------------------------------------------ helper

    async def _count(self, query: Select[Any]) -> int:
        result = await self.session.execute(query)
        return int(result.scalar() or 0)


__all__ = ["DEFAULT_WINDOW_HOURS", "SNAPSHOT_STALE_HOURS", "AdminService"]
