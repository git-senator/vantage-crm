"""Pipelines and their stages.

A pipeline is a brokerage's sales process; its stages are the columns on the
Kanban board. **Stages are rows, not an enum** — brokerages reconfigure their
funnel constantly, and an enum makes that a locking migration every time
(docs/DATABASE.md §4).

Two deviations from the design doc, both deliberate:

  * `pipeline_stages` carries `organization_id` even though it is reachable
    through `pipeline_id`. Every tenant-scoped table needs the column directly,
    because the RLS policy is `organization_id = current_organization_id()` —
    a policy that had to join through `pipelines` would be slower, and would
    silently stop working the moment someone wrote a query that did not.
  * Stage `position` is not UNIQUE. Reordering a pipeline swaps positions, and
    a unique constraint turns every swap into a three-step dance with a
    temporary value. Ordering is a presentation concern; ties break on `key`.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Pipeline(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "pipelines"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    created_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    stages: Mapped[list[PipelineStage]] = relationship(
        back_populates="pipeline",
        order_by="PipelineStage.position",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("length(name) > 0", name="ck_pipelines_name"),
        UniqueConstraint("organization_id", "name", name="uq_pipelines_org_name"),
        # Exactly one default per tenant. Partial UNIQUE rather than a service
        # check, because "promote this pipeline to default" and "create a
        # default" can race, and the loser must fail loudly rather than leave
        # a workspace with two defaults and a coin-flip for which one new deals
        # land in.
        Index(
            "uq_pipelines_one_default",
            "organization_id",
            unique=True,
            postgresql_where=text("is_default AND deleted_at IS NULL"),
        ),
        Index("ix_pipelines_org", "organization_id"),
    )

    def __repr__(self) -> str:
        return f"<Pipeline {self.id}>"


class PipelineStage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One column of a pipeline.

    No soft delete: a stage cannot simply vanish while deals point at it, and
    `deal_stage_history` references it forever. Removing a stage is a domain
    action that has to move its deals first — enforced by RESTRICT on the deal
    foreign key rather than by hope.
    """

    __tablename__ = "pipeline_stages"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    pipeline_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("pipelines.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: Stable machine name. The UI labels from `name`; analytics group by `key`.
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    #: Seeded onto a deal when it enters this stage, unless overridden.
    default_probability: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )

    # A terminal stage. Both false is an ordinary in-flight stage; both true is
    # nonsense and is rejected by ck_pipeline_stages_outcome.
    is_won: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    is_lost: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    pipeline: Mapped[Pipeline] = relationship(back_populates="stages")

    __table_args__ = (
        CheckConstraint("length(key) > 0", name="ck_pipeline_stages_key"),
        CheckConstraint("length(name) > 0", name="ck_pipeline_stages_name"),
        CheckConstraint(
            "default_probability >= 0 AND default_probability <= 100",
            name="ck_pipeline_stages_probability",
        ),
        # A stage cannot be both the win and the loss.
        CheckConstraint(
            "NOT (is_won AND is_lost)", name="ck_pipeline_stages_outcome"
        ),
        UniqueConstraint("pipeline_id", "key", name="uq_pipeline_stages_key"),
        Index("ix_pipeline_stages_pipeline_position", "pipeline_id", "position"),
        Index("ix_pipeline_stages_org", "organization_id"),
    )

    @property
    def is_terminal(self) -> bool:
        return self.is_won or self.is_lost

    def __repr__(self) -> str:
        return f"<PipelineStage {self.id} {self.key}>"


#: The pipeline every new workspace starts with.
#:
#: Mirrors the stages the prototype hard-coded, plus the loss stage it lacked —
#: a funnel with no way to lose a deal produces a 100% conversion rate, which
#: is the kind of metric that looks great and means nothing.
DEFAULT_PIPELINE_NAME = "Sales Pipeline"
DEFAULT_STAGES: tuple[dict[str, object], ...] = (
    {"key": "qualification", "name": "Qualification", "probability": 10},
    {"key": "showing", "name": "Showing", "probability": 25},
    {"key": "offer", "name": "Offer submitted", "probability": 50},
    {"key": "under_contract", "name": "Under contract", "probability": 75},
    {"key": "closing", "name": "Closing", "probability": 90},
    {"key": "closed_won", "name": "Closed won", "probability": 100, "is_won": True},
    {"key": "closed_lost", "name": "Closed lost", "probability": 0, "is_lost": True},
)
