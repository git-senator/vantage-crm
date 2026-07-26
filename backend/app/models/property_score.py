"""The stored listing-quality read — the AI's opinion, kept off the property.

The property counterpart of `lead_scores` and `deal_scores`, and the same rule
holds: the AI must never modify CRM data, so its read lives in its own table, not
on the property. A listing's own fields are the agent's; the engine's quality
score, completeness and the full explanation live here, beside the property,
never overwriting it.

One row per property, holding the quality score, its grade, the completeness
percentage, and the full explanation in `breakdown` — every signal, strength,
weakness, missing field, pricing insight and recommendation with its reason. The
numbers are reconstructible from it, which is why storing it is safe.
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


class PropertyScore(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "property_scores"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    property_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("properties.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: 0-100 listing quality. The sum of the signals in `breakdown`.
    quality: Mapped[int] = mapped_column(Integer, nullable=False)
    #: excellent / good / fair / poor — the quality band.
    grade: Mapped[str] = mapped_column(String(12), nullable=False)
    #: 0-100, the fraction of the listing checklist that is filled.
    completeness: Mapped[int] = mapped_column(Integer, nullable=False)

    breakdown: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="{}"
    )
    scorer: Mapped[str] = mapped_column(String(40), nullable=False)

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "quality >= 0 AND quality <= 100", name="ck_property_scores_quality"
        ),
        CheckConstraint(
            "completeness >= 0 AND completeness <= 100",
            name="ck_property_scores_completeness",
        ),
        UniqueConstraint("property_id", name="uq_property_scores_property"),
        # The needs-attention query: this org's listings by quality, lowest first
        # (the listings that need work surface first). Quality leads the key.
        Index("ix_property_scores_quality", "organization_id", "quality"),
    )

    def __repr__(self) -> str:
        return f"<PropertyScore property={self.property_id} quality={self.quality}>"
