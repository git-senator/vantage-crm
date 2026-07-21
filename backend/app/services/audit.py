"""Audit logging service.

Two properties matter more than convenience:

1. **Recording must never break the request.** An audit write that raises would
   turn a logging fault into an outage. Failures are logged loudly and
   swallowed. The trade-off is explicit: for a compliance regime that requires
   guaranteed capture, this becomes a hard failure — but that is a deliberate
   decision, not a default.

2. **Secrets must never reach the log.** Diffs are redacted with the same
   key list the logger uses, so a password or token in a changed field is
   scrubbed centrally rather than at each call site.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import HIGH_SEVERITY_ACTIONS
from app.core.logging import get_logger, redact, request_id_var
from app.models.audit import AuditLog

logger = get_logger(__name__)

#: Never diffed into the audit log, even if the column changes.
NEVER_DIFF_FIELDS: frozenset[str] = frozenset(
    {
        "password_hash",
        "mfa_secret",
        "token_hash",
        "settings",  # may carry integration credentials
    }
)


def json_safe(value: Any) -> Any:
    """Coerce a value into something the JSONB column can store.

    Audit metadata is assembled from ORM attributes, which are Python objects
    the JSON encoder has never heard of — `Decimal`, `UUID`, `date`. Passing
    one straight through raises `TypeError: Object of type Decimal is not JSON
    serializable` *during flush*, which is the worst possible moment: see
    `AuditService.record` for why that used to take the whole transaction with
    it.

    `Decimal` becomes a string, never a float. A price or a commission that
    round-trips through a float has already lost the precision NUMERIC exists
    to protect, and an audit log that misreports money is worse than none.
    """
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, Enum):
        return json_safe(value.value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [json_safe(item) for item in value]
    return str(value)


def build_diff(
    before: dict[str, Any], after: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Field-level {old, new} for changed fields only.

    Unchanged fields are omitted — an audit entry should answer "what changed",
    not restate the whole record.

    Comparison happens on the raw values, coercion afterwards: `Decimal("1.10")`
    and `Decimal("1.1")` are equal as Decimals but differ as strings, so
    coercing first would invent changes that never happened.
    """
    diff: dict[str, dict[str, Any]] = {}
    for key in set(before) | set(after):
        if key in NEVER_DIFF_FIELDS:
            continue
        old, new = before.get(key), after.get(key)
        if old != new:
            diff[key] = {"old": json_safe(old), "new": json_safe(new)}
    return cast("dict[str, dict[str, Any]]", redact(diff))


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        action: str,
        organization_id: UUID,
        actor_id: UUID | None = None,
        actor_email: str | None = None,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuditLog | None:
        """Append an entry. Returns None if the write failed.

        The entry joins the caller's transaction, so an audited action and its
        record commit or roll back together — an action that was rolled back
        must not leave an audit entry claiming it happened.
        """
        entry = AuditLog(
            organization_id=organization_id,
            actor_id=actor_id,
            actor_email=actor_email,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            metadata_=json_safe(redact(metadata or {})),
            ip_address=ip_address,
            user_agent=user_agent[:400] if user_agent else None,
            request_id=request_id_var.get(),
        )

        # SAVEPOINT, not a bare try/except.
        #
        # A failed flush marks the *whole* session as needing rollback, so
        # catching the exception here used to leave the caller holding a
        # poisoned transaction: every subsequent statement raised
        # PendingRollbackError and the request died anyway — with a confusing
        # error, one operation after the real one. The promise in this module's
        # docstring was therefore not true.
        #
        # `begin_nested` issues a SAVEPOINT, so a failure rolls back only the
        # audit insert and the caller's transaction survives intact.
        try:
            async with self.session.begin_nested():
                self.session.add(entry)
                await self.session.flush()
        except Exception:
            # Never let an audit failure break the request it describes.
            logger.exception(
                "audit_write_failed",
                extra={"action": action, "organization_id": str(organization_id)},
            )
            return None

        if action in HIGH_SEVERITY_ACTIONS:
            # Surfaced at a level alerting can filter on.
            logger.warning(
                "audit_high_severity",
                extra={
                    "action": action,
                    "actor_id": str(actor_id) if actor_id else None,
                    "entity_type": entity_type,
                    "entity_id": str(entity_id) if entity_id else None,
                },
            )
        else:
            logger.info("audit_recorded", extra={"action": action})

        return entry

    # ---------------------------------------------------------- querying

    def _base_query(self, organization_id: UUID) -> Select[tuple[AuditLog]]:
        return (
            select(AuditLog)
            .where(AuditLog.organization_id == organization_id)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        )

    async def list_entries(
        self,
        organization_id: UUID,
        *,
        action: str | None = None,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
        actor_id: UUID | None = None,
        limit: int = 50,
    ) -> list[AuditLog]:
        """Read the log. Filters are explicit params, never a query DSL.

        A generic filter language over an audit table is both an injection
        surface and an unbounded-query risk.
        """
        query = self._base_query(organization_id)
        if action:
            query = query.where(AuditLog.action == action)
        if entity_type:
            query = query.where(AuditLog.entity_type == entity_type)
        if entity_id:
            query = query.where(AuditLog.entity_id == entity_id)
        if actor_id:
            query = query.where(AuditLog.actor_id == actor_id)

        result = await self.session.execute(query.limit(min(limit, 200)))
        return list(result.scalars().all())
