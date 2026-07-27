"""The stored growth-intelligence read — the workspace's business health.

The org-level counterpart of `lead_scores`, `deal_scores` and `property_scores`.
The same rule holds — the AI must never modify CRM data, so its read lives in its
own table — but the grain is different: this scores the *business*, so there is
one row per organization, not one per record.

The stored row is the **organization-wide** growth health (all scopes), written
by the nightly job and refreshed when an org-wide caller reads it. A scoped
caller (a manager seeing their team) is served a live computation and does not
overwrite this canonical row. The row holds the score, its band, the period it
covered, and the full explanation in `breakdown` — every signal, revenue signal,
pipeline insight, risk and recommendation with its reason.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class GrowthScore(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "growth_scores"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    #: 0-100 business-health score. The sum of the signals in `breakdown`.
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    #: thriving / steady / at_risk / struggling — the score band.
    band: Mapped[str] = mapped_column(String(12), nullable=False)

    #: The window the score covered, echoed so a stored read is not read out of
    #: the context that produced it.
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    breakdown: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="{}"
    )
    scorer: Mapped[str] = mapped_column(String(40), nullable=False)

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 100", name="ck_growth_scores_score"),
        # One canonical org-wide row per tenant.
        UniqueConstraint("organization_id", name="uq_growth_scores_org"),
        Index("ix_growth_scores_org", "organization_id"),
    )

    def __repr__(self) -> str:
        return f"<GrowthScore org={self.organization_id} score={self.score}>"
