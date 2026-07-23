"""Admin and operational-monitoring contracts.

Nullable rates throughout. A delivery failure rate over zero messages is
undefined, and rendering it as `0%` reads as "all good" on a workspace whose
email integration is switched off entirely — the same rule the analytics
contracts follow for win rate.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SnapshotFreshness(BaseModel):
    last_snapshot_date: date | None
    #: A dead nightly job is invisible everywhere else — dashboards keep working
    #: because today is computed live, and only history stops growing.
    fresh: bool
    reason: str | None = None


class SystemHealth(BaseModel):
    #: `degraded`, never `down`: the API answered this request.
    status: str
    components: dict[str, bool]
    checked_at: datetime
    snapshot: SnapshotFreshness


class QueueStatus(BaseModel):
    reachable: bool
    #: Global, not per tenant — ARQ's queue partitions by nothing.
    queued_jobs: int | None
    #: Workers that have written a heartbeat. A deep queue with zero workers is
    #: a different incident from a deep queue with four.
    workers_seen: int | None
    open_failures: int


class JobHistoryRow(BaseModel):
    job_name: str
    failures: int
    attempts: int
    unresolved: int
    last_failed_at: datetime


class StorageUsage(BaseModel):
    attachments: int
    attachment_bytes: int
    available: int
    #: Costs a row, and possibly bytes, that nobody will ever fetch.
    pending_upload: int
    #: Somebody is probably waiting on each of these.
    quarantined: int
    unscanned: int
    export_files: int
    export_bytes: int


class FailureReason(BaseModel):
    reason: str
    count: int


class EmailDelivery(BaseModel):
    window_hours: int
    total: int
    sent: int
    failed: int
    queued: int
    failure_rate: float | None
    recent_failures: list[FailureReason] = Field(default_factory=list)


class CategoryCount(BaseModel):
    category: str
    count: int


class NotificationDelivery(BaseModel):
    window_hours: int
    total: int
    unread: int
    emailed: int
    by_category: list[CategoryCount] = Field(default_factory=list)


class ActionCount(BaseModel):
    action: str
    count: int


class ActorCount(BaseModel):
    actor_id: UUID | None
    actor_email: str
    count: int


class AuditAnalytics(BaseModel):
    window_hours: int
    total_events: int
    #: The number worth alerting on. A sustained stream from one actor against
    #: one permission is a misconfigured role or somebody probing.
    denied: int
    exports: int
    by_action: list[ActionCount] = Field(default_factory=list)
    by_actor: list[ActorCount] = Field(default_factory=list)


class AdminOverview(BaseModel):
    health: SystemHealth
    queue: QueueStatus
    jobs: list[JobHistoryRow]
    storage: StorageUsage
    email: EmailDelivery
    notifications: NotificationDelivery
    audit: AuditAnalytics
