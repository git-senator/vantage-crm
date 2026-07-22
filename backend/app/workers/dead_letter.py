"""Recording jobs that ran out of retries.

ARQ retries with exponential backoff and then gives up, logging the failure. A
log line is not enough to operate a queue: it cannot be filtered by tenant, it
ages out with log retention, and nobody sees one at 3 a.m. This module turns the
final failure into a row an admin can query, and clears it when the same work
later succeeds.

**Failures collapse by (job name, key).** A job failing every ten minutes for a
week is one problem; recording a thousand rows for it hides the other three
problems underneath. The row counts attempts and moves `last_failed_at`.

**Recording must not itself fail the worker.** If the database is what broke,
writing a row about it will not work either — the error is logged and the job
result stands. Never let the error handler become the error.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update

from app.core.logging import get_logger, redact
from app.models.job import JobFailure
from app.workers.context import unscoped_scope

logger = get_logger(__name__)

#: Enough to identify the fault; a stack trace belongs in the log, correlated
#: with everything else that happened in that job.
MAX_ERROR_MESSAGE = 2000


def job_key(*parts: object) -> str:
    """The identity of a piece of work, for collapsing repeated failures."""
    return ":".join(str(part) for part in parts if part is not None)


async def record_failure(
    *,
    job_name: str,
    key: str = "",
    organization_id: UUID | None = None,
    error: BaseException,
    attempts: int = 1,
    job_args: dict[str, Any] | None = None,
) -> None:
    """Open or update the dead-letter row for this job.

    `attempts` is how many tries this round consumed — the retry cycle calls
    this once, on exhaustion, so recording a flat 1 would understate every
    failure by the whole retry budget and make a stubborn job look like a blip.

    `job_args` is redacted with the same key list the logger uses — a job that
    happened to carry a token in its arguments must not have it land in a table
    that outlives the log retention.
    """
    now = datetime.now(UTC)
    message = f"{error}"[:MAX_ERROR_MESSAGE] or error.__class__.__name__

    try:
        async with unscoped_scope() as session:
            existing = (
                await session.execute(
                    select(JobFailure)
                    .where(JobFailure.job_name == job_name)
                    .where(JobFailure.job_key == key)
                )
            ).scalar_one_or_none()

            if existing is None:
                session.add(
                    JobFailure(
                        organization_id=organization_id,
                        job_name=job_name,
                        job_key=key,
                        job_args=redact(job_args or {}),
                        attempts=max(attempts, 1),
                        error_class=error.__class__.__name__,
                        error_message=message,
                        first_failed_at=now,
                        last_failed_at=now,
                    )
                )
            else:
                # A previously resolved row that fails again reopens rather
                # than starting a new one — the history of a flapping job is
                # more useful in one place than scattered across rows.
                existing.attempts += max(attempts, 1)
                existing.error_class = error.__class__.__name__
                existing.error_message = message
                existing.last_failed_at = now
                existing.resolved_at = None
    except Exception:
        logger.exception(
            "dead_letter_write_failed", extra={"job_name": job_name, "job_key": key}
        )


async def clear_failure(*, job_name: str, key: str = "") -> None:
    """Mark this job/key resolved after a successful run.

    The row is kept rather than deleted: "this was broken for six hours on
    Tuesday" is a question worth being able to answer after the fact.
    """
    try:
        async with unscoped_scope() as session:
            await session.execute(
                update(JobFailure)
                .where(JobFailure.job_name == job_name)
                .where(JobFailure.job_key == key)
                .where(JobFailure.resolved_at.is_(None))
                .values(resolved_at=datetime.now(UTC))
            )
    except Exception:
        logger.exception(
            "dead_letter_clear_failed", extra={"job_name": job_name, "job_key": key}
        )
