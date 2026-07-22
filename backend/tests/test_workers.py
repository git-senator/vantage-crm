"""Background jobs — retry, dead-lettering, sweeps and the scan pipeline.

What these tests are really about is the two things that make a queue safe to
run unattended: **a failing job must retry and then be recorded somewhere a
person looks**, and **a job must not be able to see or touch more data than a
request could**.

`TestRetryAndDeadLetter` covers the first. It matters more than it looks,
because ARQ does not retry ordinary exceptions on its own — the behaviour lives
in `@job`, so a test that only checked `retry_jobs=True` would prove nothing.

`TestScanPipeline` and `TestSweeps` cover the second, and they are the reason
the sweeps iterate tenants explicitly: an unscoped worker session sees nothing
under RLS, which looks exactly like "there was no work to do".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from arq import Retry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.permissions import Scope
from app.models.audit import AuditLog
from app.models.job import JobFailure
from app.schemas.attachment import AttachmentCreate
from app.schemas.lead import LeadCreate
from app.services.attachment import AttachmentService
from app.services.lead import LeadService
from app.services.storage.scanning import EicarSignatureScanner
from app.workers.context import active_organization_ids, system_context
from app.workers.dead_letter import clear_failure, job_key, record_failure
from app.workers.runner import BACKOFF_CAP_SECONDS, backoff_seconds, job

PDF = b"%PDF-1.7\n1 0 obj\n<</Type/Catalog>>\nendobj\n"

# The EICAR test string, assembled at runtime so this file does not itself trip
# a scanner watching the repository.
EICAR = (
    b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-" b"STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)


class TestBackoff:
    """Pure unit tests — no queue, no database."""

    def test_backoff_grows_and_is_capped(self) -> None:
        early = backoff_seconds(1)
        later = backoff_seconds(6)
        assert early < later
        assert later <= BACKOFF_CAP_SECONDS

    def test_backoff_is_jittered(self) -> None:
        """Identical delays across a burst of failures is a synchronised load
        spike aimed at whatever is already struggling."""
        samples = {backoff_seconds(4) for _ in range(20)}
        assert len(samples) > 1

    def test_jitter_never_exceeds_the_unjittered_delay(self) -> None:
        assert all(backoff_seconds(3) <= 8.0 for _ in range(50))


class TestRetryAndDeadLetter:
    pytestmark = pytest.mark.integration

    async def test_a_failing_job_asks_to_be_retried(self, db: AsyncSession) -> None:
        """ARQ re-queues on `Retry` and on nothing else, so this is the whole
        retry mechanism — not a configuration flag."""

        @job()
        async def flaky(ctx: dict) -> str:  # type: ignore[type-arg]
            raise RuntimeError("dependency unavailable")

        with pytest.raises(Retry):
            await flaky({"job_try": 1})

        # Nothing recorded yet: a blip that resolves itself must not appear in
        # a view people are meant to act on.
        rows = (await db.execute(select(JobFailure))).scalars().all()
        assert rows == []

    async def test_the_last_attempt_dead_letters(self, db: AsyncSession) -> None:
        @job(max_tries=3)
        async def doomed(ctx: dict, target: str) -> str:  # type: ignore[type-arg]
            raise ValueError("permanently broken")

        with pytest.raises(ValueError, match="permanently broken"):
            await doomed({"job_try": 3}, "record-1")

        row = (await db.execute(select(JobFailure))).scalar_one()
        assert row.job_name == "doomed"
        assert row.job_key == "record-1"
        assert row.error_class == "ValueError"
        assert row.resolved_at is None
        # The whole retry budget, not a flat 1 — a stubborn job must not read
        # as a blip in the view someone is triaging from.
        assert row.attempts == 3

    async def test_repeated_failures_collapse_into_one_row(
        self, db: AsyncSession
    ) -> None:
        """A job failing every ten minutes for a week is one problem. A
        thousand rows for it would bury the other three."""
        for _ in range(3):
            await record_failure(
                job_name="noisy", key="k", error=RuntimeError("again")
            )

        row = (await db.execute(select(JobFailure))).scalar_one()
        assert row.attempts == 3
        assert row.first_failed_at <= row.last_failed_at

    async def test_success_resolves_an_open_failure(self, db: AsyncSession) -> None:
        await record_failure(job_name="flappy", key="k", error=RuntimeError("x"))
        await clear_failure(job_name="flappy", key="k")

        row = (await db.execute(select(JobFailure))).scalar_one()
        # Kept, not deleted: "this was broken for six hours on Tuesday" is a
        # question worth being able to answer afterwards.
        assert row.resolved_at is not None

    async def test_a_resolved_failure_reopens_rather_than_duplicating(
        self, db: AsyncSession
    ) -> None:
        await record_failure(job_name="flappy", key="k", error=RuntimeError("x"))
        await clear_failure(job_name="flappy", key="k")
        await record_failure(job_name="flappy", key="k", error=RuntimeError("y"))

        row = (await db.execute(select(JobFailure))).scalar_one()
        assert row.resolved_at is None
        assert row.attempts == 2

    async def test_a_successful_job_records_nothing(self, db: AsyncSession) -> None:
        @job()
        async def fine(ctx: dict) -> str:  # type: ignore[type-arg]
            return "ok"

        assert await fine({"job_try": 1}) == "ok"
        assert (await db.execute(select(JobFailure))).scalars().all() == []

    def test_job_key_identifies_the_work_not_the_attempt(self) -> None:
        assert job_key("a", "b") == "a:b"
        assert job_key("a", None, "b") == "a:b"


class TestSystemContext:
    """Machine work runs through the same services as a request, with an
    explicit context — not a parallel implementation with looser rules."""

    pytestmark = pytest.mark.integration

    def test_a_system_context_carries_only_what_was_granted(self) -> None:
        from uuid import uuid4

        auth = system_context(uuid4(), "documents.view")
        assert auth.can("documents.view")
        assert not auth.can("deals.manage")
        assert auth.scope_for("documents.view") is Scope.ALL

    def test_a_system_context_has_no_actor(self) -> None:
        """There is no user behind a cron tick, and faking one with a
        placeholder id is how a query later joins on a user that never
        existed."""
        from uuid import uuid4

        assert system_context(uuid4(), "documents.view").user_id is None

    async def test_a_narrow_scope_without_an_actor_matches_nothing(
        self, db: AsyncSession
    ) -> None:
        """Fails closed. The alternative reading — "no actor, so no
        restriction" — is how a job quietly gets more access than any user."""
        from uuid import uuid4

        from app.services.rbac import RbacService

        auth = system_context(uuid4(), "documents.view")
        assert await RbacService(db).owner_ids_for_scope(auth, Scope.OWN) == []
        assert await RbacService(db).owner_ids_for_scope(auth, Scope.TEAM) == []


class TestTenantEnumeration:
    pytestmark = pytest.mark.integration

    async def test_the_tenant_list_is_readable_without_tenant_context(
        self, db: AsyncSession, organization, other_organization
    ) -> None:  # type: ignore[no-untyped-def]
        """The whole reason `list_active_organization_ids()` exists: a sweep
        must find every tenant without being handed BYPASSRLS."""
        ids = await active_organization_ids(db)
        assert organization.id in ids
        assert other_organization.id in ids


class TestScanner:
    async def test_eicar_is_detected(self) -> None:
        result = await EicarSignatureScanner().scan(b"prefix" + EICAR + b"suffix")
        assert result.verdict == "infected"
        assert result.signature == "EICAR-Test-File"

    async def test_an_ordinary_file_is_clean(self) -> None:
        assert (await EicarSignatureScanner().scan(PDF)).verdict == "clean"


class TestScanPipeline:
    """Scanning is off by default, so these drive the service directly with it
    on — the job function is a thin wrapper over exactly these calls."""

    pytestmark = pytest.mark.integration

    @pytest.fixture
    async def scanning_settings(self, storage_settings):  # type: ignore[no-untyped-def]
        return storage_settings.model_copy(update={"MALWARE_SCAN_ENABLED": True})

    @pytest.fixture
    async def lead_record(self, db: AsyncSession, admin):  # type: ignore[no-untyped-def]
        user, auth = admin
        return await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Sana", last_name="Kaur"), user
        )

    async def _registered(self, db, auth, user, lead, storage, settings, payload):  # type: ignore[no-untyped-def]
        service = AttachmentService(db, auth, storage=storage, settings=settings)
        attachment, _ = await service.register(
            AttachmentCreate(
                entity_type="lead",
                entity_id=lead.id,
                filename="offer.pdf",
                content_type="application/pdf",
            ),
            user,
        )
        upload = service._presign_upload_for(attachment)
        await storage.write(
            storage.resolve(upload.url, "put"),
            payload,
            content_type="application/pdf",
        )
        return service, attachment

    async def test_a_verified_upload_is_held_until_it_is_scanned(
        self, db, admin, lead_record, object_storage, scanning_settings
    ) -> None:  # type: ignore[no-untyped-def]
        """Between "these bytes are the type they claim" and "these bytes are
        not malware" there is a window, and a file must not be servable in it."""
        user, auth = admin
        service, attachment = await self._registered(
            db, auth, user, lead_record, object_storage, scanning_settings, PDF
        )
        finalized = await service.finalize(attachment.id, user)

        assert finalized.status == "pending_upload"
        assert finalized.scan_status == "pending"
        # Verified, though — size and checksum are already established.
        assert finalized.size_bytes == len(PDF)

    async def test_a_clean_verdict_publishes(
        self, db, admin, lead_record, object_storage, scanning_settings
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service, attachment = await self._registered(
            db, auth, user, lead_record, object_storage, scanning_settings, PDF
        )
        await service.finalize(attachment.id, user)
        published = await service.apply_scan_result(
            attachment.id, verdict="clean", scanner="eicar"
        )

        assert published.status == "available"
        assert published.scan_status == "clean"
        assert published.available_at is not None

    async def test_an_infected_file_is_quarantined_deleted_and_audited(
        self, db, admin, lead_record, object_storage, scanning_settings
    ) -> None:  # type: ignore[no-untyped-def]
        """The exit criterion, in one test. A quarantined file is not a file
        kept somewhere safer — it is a file that no longer exists."""
        user, auth = admin
        service, attachment = await self._registered(
            db, auth, user, lead_record, object_storage, scanning_settings, PDF
        )
        await service.finalize(attachment.id, user)

        quarantined = await service.apply_scan_result(
            attachment.id,
            verdict="infected",
            signature="EICAR-Test-File",
            scanner="eicar",
        )

        assert quarantined.status == "quarantined"
        assert quarantined.scan_status == "infected"
        assert object_storage.keys() == []

        entry = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == AuditAction.DOCUMENT_QUARANTINED
                )
            )
        ).scalar_one()
        assert entry.metadata_["signature"] == "EICAR-Test-File"

    async def test_a_quarantined_file_is_never_served(
        self, db, admin, lead_record, object_storage, scanning_settings
    ) -> None:  # type: ignore[no-untyped-def]
        from app.core.exceptions import ConflictError

        user, auth = admin
        service, attachment = await self._registered(
            db, auth, user, lead_record, object_storage, scanning_settings, PDF
        )
        await service.finalize(attachment.id, user)
        await service.apply_scan_result(
            attachment.id, verdict="infected", signature="EICAR-Test-File"
        )

        with pytest.raises(ConflictError):
            await service.presign_download(attachment.id, user)

    async def test_a_scanner_outage_leaves_the_file_unpublished(
        self, db, admin, lead_record, object_storage, scanning_settings
    ) -> None:  # type: ignore[no-untyped-def]
        """"We could not check it" is not "it is fine". A scanner being down
        must never publish files."""
        user, auth = admin
        service, attachment = await self._registered(
            db, auth, user, lead_record, object_storage, scanning_settings, PDF
        )
        await service.finalize(attachment.id, user)
        result = await service.apply_scan_result(attachment.id, verdict="failed")

        assert result.status == "pending_upload"
        assert result.scan_status == "failed"
        # The bytes survive — a scanner outage is not a reason to destroy a
        # customer's upload.
        assert object_storage.keys()

    async def test_the_scan_backlog_is_findable(
        self, db, admin, lead_record, object_storage, scanning_settings
    ) -> None:  # type: ignore[no-untyped-def]
        """The database, not Redis, is the record of outstanding work — which
        is what makes `enqueue` safe to be best-effort."""
        user, auth = admin
        service, attachment = await self._registered(
            db, auth, user, lead_record, object_storage, scanning_settings, PDF
        )
        await service.finalize(attachment.id, user)

        pending = await service.list_pending_scan()
        assert [row.id for row in pending] == [attachment.id]


class TestSweeps:
    pytestmark = pytest.mark.integration

    @pytest.fixture
    async def lead_record(self, db: AsyncSession, admin):  # type: ignore[no-untyped-def]
        user, auth = admin
        return await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Ravi", last_name="Menon"), user
        )

    async def test_an_abandoned_registration_is_found_and_closed(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        """Without this, an abandoned file picker leaves a row that reads
        `Processing` forever."""
        user, auth = admin
        service = AttachmentService(
            db, auth, storage=object_storage, settings=storage_settings
        )
        attachment, _ = await service.register(
            AttachmentCreate(
                entity_type="lead",
                entity_id=lead_record.id,
                filename="never-arrived.pdf",
                content_type="application/pdf",
            ),
            user,
        )
        attachment.upload_expires_at = datetime.now(UTC) - timedelta(minutes=5)
        await db.flush()

        abandoned = await service.list_abandoned()
        assert [row.id for row in abandoned] == [attachment.id]

        await service.expire_abandoned(attachment)
        assert attachment.status == "failed"
        assert attachment.upload_expires_at is None

    async def test_the_sweep_deletes_a_late_object(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        """A PUT that completed just as the window closed would otherwise leave
        an object no row will ever reference again."""
        user, auth = admin
        service = AttachmentService(
            db, auth, storage=object_storage, settings=storage_settings
        )
        attachment, _ = await service.register(
            AttachmentCreate(
                entity_type="lead",
                entity_id=lead_record.id,
                filename="late.pdf",
                content_type="application/pdf",
            ),
            user,
        )
        upload = service._presign_upload_for(attachment)
        await object_storage.write(
            object_storage.resolve(upload.url, "put"),
            PDF,
            content_type="application/pdf",
        )
        assert object_storage.keys()

        await service.expire_abandoned(attachment)
        assert object_storage.keys() == []

    async def test_a_live_registration_is_left_alone(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = AttachmentService(
            db, auth, storage=object_storage, settings=storage_settings
        )
        await service.register(
            AttachmentCreate(
                entity_type="lead",
                entity_id=lead_record.id,
                filename="in-progress.pdf",
                content_type="application/pdf",
            ),
            user,
        )
        assert await service.list_abandoned() == []
