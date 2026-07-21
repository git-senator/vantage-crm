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


def build_diff(
    before: dict[str, Any], after: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Field-level {old, new} for changed fields only.

    Unchanged fields are omitted — an audit entry should answer "what changed",
    not restate the whole record.
    """
    diff: dict[str, dict[str, Any]] = {}
    for key in set(before) | set(after):
        if key in NEVER_DIFF_FIELDS:
            continue
        old, new = before.get(key), after.get(key)
        if old != new:
            diff[key] = {"old": old, "new": new}
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
            metadata_=redact(metadata or {}),
            ip_address=ip_address,
            user_agent=user_agent[:400] if user_agent else None,
            request_id=request_id_var.get(),
        )

        try:
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
