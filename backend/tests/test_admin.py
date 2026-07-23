"""Administration and operational monitoring.

What these pin down:

  * **`settings.manage` gates everything.** An agent cannot read the workspace's
    queue depth, storage bill or audit analytics.
  * **Absence of a problem is distinguishable from absence of data.** A rate
    over zero messages is `null`, not `0%`; a workspace with no snapshot history
    is not "stale".
  * **A dead nightly job is visible.** It is invisible everywhere else, because
    dashboards keep working from live data while only history stops growing.
  * **Tenant scope holds** — one workspace's operator sees none of another's
    volumes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.exceptions import PermissionDeniedError
from app.core.permissions import Scope
from app.models.analytics import MetricSnapshot
from app.models.job import JobFailure
from app.models.organization import Organization
from app.services.admin import SNAPSHOT_STALE_HOURS, AdminService
from app.services.audit import AuditService
from app.services.rbac import AuthorizationContext
from tests.conftest import make_user

pytestmark = pytest.mark.integration


def _admin_auth(organization: Organization, user_id) -> AuthorizationContext:  # type: ignore[no-untyped-def]
    return AuthorizationContext(
        user_id=user_id,
        organization_id=organization.id,
        role_keys=("admin",),
        grants={"settings.manage": Scope.ALL, "settings.view": Scope.ALL},
    )


class TestPermissions:
    async def test_an_agent_cannot_read_operational_data(
        self, db: AsyncSession, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, "agent@vantage.example")
        auth = AuthorizationContext(
            user_id=user.id,
            organization_id=organization.id,
            role_keys=("agent",),
            grants={"leads.view": Scope.OWN, "reports.view": Scope.OWN},
        )
        service = AdminService(db, auth)

        for call in (
            service.system_health(),
            service.queue_health(),
            service.storage_usage(),
            service.email_delivery(),
            service.notification_delivery(),
            service.audit_analytics(),
        ):
            with pytest.raises(PermissionDeniedError):
                await call


class TestSnapshotFreshness:
    async def test_a_workspace_with_no_history_is_not_stale(
        self, db: AsyncSession, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        """A workspace created this morning has no snapshots and no problem.

        Reporting that as staleness would page somebody on their first day.
        """
        user = await make_user(db, organization, "new@vantage.example")
        health = await AdminService(db, _admin_auth(organization, user.id)).system_health()

        assert health["snapshot"]["last_snapshot_date"] is None
        assert health["snapshot"]["fresh"] is True

    async def test_a_stale_snapshot_degrades_the_status(
        self, db: AsyncSession, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        """The one place a silently dead nightly job shows up."""
        user = await make_user(db, organization, "ops@vantage.example")
        old = (datetime.now(UTC) - timedelta(days=5)).date()
        db.add(
            MetricSnapshot(
                organization_id=organization.id,
                owner_id=user.id,
                snapshot_date=old,
                metric_key="leads_created",
                value=1,
            )
        )
        await db.flush()

        health = await AdminService(db, _admin_auth(organization, user.id)).system_health()
        assert health["snapshot"]["fresh"] is False
        assert health["components"]["analytics_snapshot"] is False
        assert health["status"] == "degraded"

    async def test_a_recent_snapshot_is_fresh(
        self, db: AsyncSession, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, "fresh@vantage.example")
        db.add(
            MetricSnapshot(
                organization_id=organization.id,
                owner_id=user.id,
                snapshot_date=datetime.now(UTC).date(),
                metric_key="leads_created",
                value=1,
            )
        )
        await db.flush()

        health = await AdminService(db, _admin_auth(organization, user.id)).system_health()
        assert health["snapshot"]["fresh"] is True
        assert SNAPSHOT_STALE_HOURS >= 24


class TestJobHistory:
    async def test_failures_are_grouped_by_job_name(
        self, db: AsyncSession, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        """An operator wants to know which job is broken, not forty copies of
        the same dead letter."""
        user = await make_user(db, organization, "jobs@vantage.example")
        now = datetime.now(UTC)
        for index in range(3):
            db.add(
                JobFailure(
                    organization_id=organization.id,
                    job_name="send_email",
                    job_key=f"key-{index}",
                    job_args={},
                    attempts=2,
                    error_class="StorageError",
                    error_message="boom",
                    first_failed_at=now,
                    last_failed_at=now,
                )
            )
        await db.flush()

        rows = await AdminService(db, _admin_auth(organization, user.id)).job_history()
        assert len(rows) == 1
        assert rows[0]["job_name"] == "send_email"
        assert rows[0]["failures"] == 3
        assert rows[0]["unresolved"] == 3
        assert rows[0]["attempts"] == 6

    async def test_infrastructure_failures_with_no_tenant_are_visible(
        self, db: AsyncSession, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        """A sweep that belongs to no tenant is the failure most worth seeing,
        and it carries no customer data by construction."""
        user = await make_user(db, organization, "infra@vantage.example")
        now = datetime.now(UTC)
        db.add(
            JobFailure(
                organization_id=None,
                job_name="sweep_workflow_events",
                job_key="sweep-1",
                job_args={},
                attempts=1,
                error_class="TimeoutError",
                error_message="slow",
                first_failed_at=now,
                last_failed_at=now,
            )
        )
        await db.flush()

        rows = await AdminService(db, _admin_auth(organization, user.id)).job_history()
        assert [row["job_name"] for row in rows] == ["sweep_workflow_events"]

    async def test_the_window_excludes_older_failures(
        self, db: AsyncSession, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, "window@vantage.example")
        old = datetime.now(UTC) - timedelta(days=10)
        db.add(
            JobFailure(
                organization_id=organization.id,
                job_name="ancient_job",
                job_key="old-1",
                job_args={},
                attempts=1,
                error_class="ValueError",
                error_message="old",
                first_failed_at=old,
                last_failed_at=old,
            )
        )
        await db.flush()

        rows = await AdminService(db, _admin_auth(organization, user.id)).job_history(hours=24)
        assert rows == []


class TestDelivery:
    async def test_a_failure_rate_over_nothing_is_null_not_zero(
        self, db: AsyncSession, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        """0% reads as "all good" on a workspace whose email is switched off."""
        user = await make_user(db, organization, "mail@vantage.example")
        result = await AdminService(db, _admin_auth(organization, user.id)).email_delivery()

        assert result["total"] == 0
        assert result["failure_rate"] is None

    async def test_notification_volumes_are_broken_down_by_category(
        self, db: AsyncSession, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        from app.models.notification import Notification

        user = await make_user(db, organization, "notify@vantage.example")
        for category in ("lead", "lead", "system"):
            db.add(
                Notification(
                    organization_id=organization.id,
                    user_id=user.id,
                    category=category,
                    type="test",
                    title="Something happened",
                )
            )
        await db.flush()

        result = await AdminService(db, _admin_auth(organization, user.id)).notification_delivery()

        assert result["total"] == 3
        assert result["unread"] == 3
        counts = {row["category"]: row["count"] for row in result["by_category"]}
        assert counts == {"lead": 2, "system": 1}


class TestAuditAnalytics:
    async def test_denials_are_counted_separately(
        self, db: AsyncSession, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        """A sustained stream of denials is a misconfigured role or somebody
        probing, and neither is visible in a total event count."""
        user = await make_user(db, organization, "audit@vantage.example")
        audit = AuditService(db)

        await audit.record(
            action=AuditAction.RECORD_CREATED,
            organization_id=organization.id,
            actor_id=user.id,
            actor_email=user.email,
        )
        for _ in range(2):
            await audit.record(
                action=AuditAction.PERMISSION_DENIED,
                organization_id=organization.id,
                actor_id=user.id,
                actor_email=user.email,
            )
        await db.flush()

        result = await AdminService(db, _admin_auth(organization, user.id)).audit_analytics()
        assert result["total_events"] == 3
        assert result["denied"] == 2

    async def test_exports_are_surfaced(self, db: AsyncSession, organization: Organization) -> None:  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, "export@vantage.example")
        await AuditService(db).record(
            action=AuditAction.RECORD_EXPORTED,
            organization_id=organization.id,
            actor_id=user.id,
            actor_email=user.email,
            metadata={"rows": 400},
        )
        await db.flush()

        result = await AdminService(db, _admin_auth(organization, user.id)).audit_analytics()
        assert result["exports"] == 1
        assert result["by_actor"][0]["actor_email"] == user.email


class TestStorage:
    async def test_stuck_and_quarantined_files_are_broken_out(
        self, db: AsyncSession, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        """The two states that cost money without delivering value."""
        import uuid

        from app.models.attachment import Attachment

        user = await make_user(db, organization, "files@vantage.example")
        db.add_all(
            [
                Attachment(
                    organization_id=organization.id,
                    uploaded_by=user.id,
                    entity_type="lead",
                    entity_id=uuid.uuid4(),
                    filename="ok.pdf",
                    content_type="application/pdf",
                    size_bytes=1000,
                    # An `available` attachment must have an object behind it —
                    # ck_attachments_available_has_object enforces that a row
                    # cannot claim to be downloadable with no bytes.
                    storage_key="org/x/lead/y/z/ok.pdf",
                    available_at=datetime.now(UTC),
                    status="available",
                    scan_status="clean",
                ),
                Attachment(
                    organization_id=organization.id,
                    uploaded_by=user.id,
                    entity_type="lead",
                    entity_id=uuid.uuid4(),
                    filename="stuck.pdf",
                    content_type="application/pdf",
                    status="pending_upload",
                    scan_status="pending",
                ),
            ]
        )
        await db.flush()

        result = await AdminService(db, _admin_auth(organization, user.id)).storage_usage()
        assert result["attachments"] == 2
        assert result["available"] == 1
        assert result["pending_upload"] == 1
        assert result["attachment_bytes"] == 1000


class TestTenantIsolation:
    async def test_one_workspaces_operator_sees_none_of_anothers_volumes(
        self, db: AsyncSession, organization: Organization, other_organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        mine = await make_user(db, organization, "mine@vantage.example")
        theirs = await make_user(db, other_organization, "theirs@meridian.example")

        await AuditService(db).record(
            action=AuditAction.RECORD_CREATED,
            organization_id=other_organization.id,
            actor_id=theirs.id,
            actor_email=theirs.email,
        )
        await db.flush()

        result = await AdminService(db, _admin_auth(organization, mine.id)).audit_analytics()
        assert result["total_events"] == 0
