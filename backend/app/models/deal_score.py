"""The stored deal-health read — the AI's opinion, kept off the deal.

The deal counterpart of `lead_scores`, and the same rule holds: the AI must never
modify CRM data, so its read lives in its own table, not on the deal. A deal's
own `probability` is the agent's (or the stage's); the engine's *inferred* win
probability lives here, beside the deal, never overwriting it.

One row per deal, holding health, the inferred win probability, the forecast
contribution, and the full explanation in `breakdown` — every signal, factor,
risk, missing field and recommendation with its reason. The numbers are
reconstructible from it, which is why storing it is safe.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class DealScore(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "deal_scores"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    deal_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("deals.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: 0-100 overall health. The sum of the signals in `breakdown`.
    health: Mapped[int] = mapped_column(Integer, nullable=False)
    #: healthy / at_risk / critical, or the terminal status for a closed deal.
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    #: 0-100 inferred win probability — the stage baseline adjusted by the stored
    #: factors. Distinct from the deal's own `probability`.
    win_probability: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The deal's weighted forecast contribution (value x win probability), or
    #: NULL if the deal has no value. NUMERIC because it is money.
    forecast_value: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    #: A flag lifted out of the breakdown so the at-risk query can filter on it
    #: without reading JSON.
    is_stalled: Mapped[bool] = mapped_column(
        postgresql.BOOLEAN, nullable=False, server_default="false"
    )

    breakdown: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="{}"
    )
    scorer: Mapped[str] = mapped_column(String(40), nullable=False)

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("health >= 0 AND health <= 100", name="ck_deal_scores_health"),
        CheckConstraint(
            "win_probability >= 0 AND win_probability <= 100",
            name="ck_deal_scores_win",
        ),
        UniqueConstraint("deal_id", name="uq_deal_scores_deal"),
        # The at-risk query: this org's deals by health, lowest first (worst
        # deals surface first). Health leads the ordering key.
        Index("ix_deal_scores_health", "organization_id", "health"),
    )

    def __repr__(self) -> str:
        return f"<DealScore deal={self.deal_id} health={self.health} win={self.win_probability}>"
