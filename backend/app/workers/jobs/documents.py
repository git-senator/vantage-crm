"""Document jobs: scan uploads, sweep what was abandoned.

Three jobs, and the relationship between them is the design:

  * `scan_attachment` is enqueued by `finalize` and does the work.
  * `sweep_scan_backlog` re-finds anything still `pending` and enqueues it
    again. This is what makes the lost-enqueue path survivable — the queue is
    allowed to drop a message because the database, not Redis, is the record of
    what needs doing.
  * `sweep_abandoned_uploads` closes registrations whose window elapsed, and
    deletes any object behind them.

Every one of them iterates tenants explicitly and binds each before touching
data. A sweep written without that runs cleanly and does nothing, because RLS
returns no rows to an unscoped session — a failure mode that looks exactly like
"there was nothing to do".
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.attachment import AttachmentService
from app.services.storage import ObjectNotFoundError, StorageError, get_object_storage
from app.services.storage.scanning import ScanResult, build_scanner
from app.workers.context import (
    active_organization_ids,
    system_context,
    tenant_scope,
    unscoped_scope,
)
from app.workers.queue import JobName, enqueue
from app.workers.runner import job

logger = get_logger(__name__)

#: The permissions a document job runs with. Narrow on purpose: the scanner has
#: no business being able to touch a deal.
_DOCUMENT_GRANTS = ("documents.view", "documents.manage")


@job(organization_arg=1)
async def scan_attachment(
    ctx: dict[str, Any], attachment_id: str, organization_id: str
) -> str:
    """Scan one uploaded object and resolve its fate.

    Returns the verdict so a run shows up meaningfully in the job result log.

    Raises on a storage or database fault, deliberately — that is what ARQ's
    retry-with-backoff is for, and the file stays unpublished in the meantime.
    A *scanner* fault is different: it is reported as a `failed` verdict rather
    than an exception, because retrying a scanner that is down every ten
    seconds helps nobody and the next sweep will pick the file up anyway.
    """
    settings = get_settings()
    scanner = build_scanner(settings)
    storage = get_object_storage()
    organization = UUID(organization_id)
    auth = system_context(organization, *_DOCUMENT_GRANTS)

    async with tenant_scope(organization) as session:
        service = AttachmentService(session, auth, storage=storage, settings=settings)
        attachment = await service.attachments.get(UUID(attachment_id), organization)

        if attachment is None or attachment.deleted_at is not None:
            # Deleted between finalize and this run. Not an error.
            return "gone"
        if attachment.scan_status != "pending" or attachment.storage_key is None:
            # Already resolved — a duplicate enqueue, or the backlog sweep
            # racing the direct enqueue. Idempotent by design.
            return attachment.scan_status

        if (attachment.size_bytes or 0) > settings.MALWARE_SCAN_MAX_BYTES:
            # Too large to buffer. Left unpublished rather than waved through:
            # "we could not check it" is not "it is fine".
            result = ScanResult(
                verdict="failed",
                signature="file-too-large-to-scan",
                scanner=scanner.name,
            )
        else:
            try:
                content = await storage.read(
                    attachment.storage_key, max_bytes=settings.MALWARE_SCAN_MAX_BYTES
                )
            except ObjectNotFoundError:
                # The object is gone but the row says pending. Nothing to
                # scan and nothing to serve; close it out as failed.
                result = ScanResult(
                    verdict="failed", signature="object-missing", scanner=scanner.name
                )
            else:
                result = await scanner.scan(content)

        await service.apply_scan_result(
            attachment.id,
            verdict=result.verdict,
            signature=result.signature,
            scanner=result.scanner,
        )
        return result.verdict


@job(max_tries=2)
async def sweep_scan_backlog(ctx: dict[str, Any]) -> int:
    """Re-enqueue scans that were never picked up. Returns how many.

    The safety net under `enqueue`'s deliberate best-effort behaviour: because
    the `pending` rows are the real record of outstanding work, a queue that
    lost a message costs a delay rather than a file stuck forever in limbo.
    """
    settings = get_settings()
    if not settings.MALWARE_SCAN_ENABLED:
        return 0

    queued = 0
    async with unscoped_scope() as session:
        organizations = await active_organization_ids(session)

    for organization in organizations:
        auth = system_context(organization, *_DOCUMENT_GRANTS)
        async with tenant_scope(organization) as session:
            service = AttachmentService(session, auth, settings=settings)
            pending = await service.list_pending_scan(limit=200)

        for attachment in pending:
            # Same derived id as `finalize` uses, so re-enqueueing something
            # already queued is a no-op rather than a second scan.
            if await enqueue(
                JobName.SCAN_ATTACHMENT,
                str(attachment.id),
                str(organization),
                job_id=f"scan:{attachment.id}",
            ):
                queued += 1

    if queued:
        logger.info("scan_backlog_swept", extra={"queued": queued})
    return queued


@job(max_tries=2)
async def sweep_abandoned_uploads(ctx: dict[str, Any]) -> int:
    """Close registrations whose upload window elapsed. Returns how many.

    Without this, every abandoned file picker leaves a row that reads
    `Processing` forever and, if the PUT happened to land after the window
    closed, an object nothing will ever reference again.
    """
    settings = get_settings()
    storage = get_object_storage()
    swept = 0

    async with unscoped_scope() as session:
        organizations = await active_organization_ids(session)

    for organization in organizations:
        auth = system_context(organization, *_DOCUMENT_GRANTS)
        async with tenant_scope(organization) as session:
            service = AttachmentService(
                session, auth, storage=storage, settings=settings
            )
            abandoned = await service.list_abandoned(limit=500)
            for attachment in abandoned:
                try:
                    await service.expire_abandoned(attachment)
                except StorageError:
                    # One tenant's storage trouble must not abort the sweep for
                    # everyone else; the next tick retries this row.
                    logger.exception(
                        "abandoned_sweep_failed",
                        extra={"attachment_id": str(attachment.id)},
                    )
                    continue
                swept += 1

    if swept:
        logger.info("abandoned_uploads_swept", extra={"count": swept})
    return swept
