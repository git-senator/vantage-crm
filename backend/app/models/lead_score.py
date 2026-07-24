"""The stored lead score — the AI's read on a lead, kept apart from the lead.

One row per lead, holding the latest deterministic score and its full
explanation. It exists so prioritisation can rank a whole book of leads without
recomputing each one, and so a background sweep can keep scores fresh as leads
change.

**It is deliberately not a column on `leads`.** The AI must never modify CRM
data (SECURITY.md §5), and a lead's own `score` field is the agent's to set. The
AI's opinion lives in its own table, next to the lead but not on it, so the two
can never be confused and turning the AI off leaves the lead untouched.

The `breakdown` JSONB carries every signal, risk, missing field and
recommendation with its reason — the score is reconstructible from it, because
the score *is* the sum of it. Nothing here is a black box, so nothing here needs
to be trusted on faith.
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


class LeadScore(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "lead_scores"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: CASCADE: a score for a deleted lead is meaningless, so it goes with the
    #: lead rather than lingering as an orphan the prioritiser would surface.
    lead_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("leads.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: 0-100. The number, and the sole reason it is safe to sort on is that it is
    #: reproducible from `breakdown`.
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The engine's inferred temperature — distinct from the agent's on the lead.
    temperature: Mapped[str] = mapped_column(String(10), nullable=False)
    qualification: Mapped[str] = mapped_column(String(20), nullable=False)
    priority: Mapped[str] = mapped_column(String(10), nullable=False)
    buying_intent: Mapped[str] = mapped_column(String(10), nullable=False)

    #: The whole explanation: signals, risks, missing info, recommendations, each
    #: with its reason. JSONB because the shape is a nested document read as a
    #: unit, not queried field-by-field.
    breakdown: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="{}"
    )

    #: Which scorer produced this — rules-v1 today, an ML version tomorrow. On
    #: the row so a mixed population during a rollout is legible.
    scorer: Mapped[str] = mapped_column(String(40), nullable=False)

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 100", name="ck_lead_scores_range"),
        # One current score per lead. The service upserts on this, so a rescore
        # replaces the row rather than accumulating history — the latest read is
        # what prioritisation wants, and history, if ever needed, is a separate
        # append-only table, not this one silently growing.
        UniqueConstraint("lead_id", name="uq_lead_scores_lead"),
        # The prioritisation query: this org's leads, highest score first.
        Index(
            "ix_lead_scores_ranking",
            "organization_id",
            "score",
        ),
    )

    def __repr__(self) -> str:
        return f"<LeadScore lead={self.lead_id} score={self.score}>"
