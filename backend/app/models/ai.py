"""AI jobs — one row per completion, for cost accounting and diagnostics.

Named `ai_jobs` because SECURITY.md §5 named it: "every call's cost recorded in
`ai_jobs`". A row is written for every completion the system dispatches, whether
it succeeded, failed, or was refused by the budget before it ever left — the
record of *attempted* egress is as much the point as the record of spend.

**What is deliberately not here: the prompt and the completion text.** This
table is a ledger, not a transcript. Storing the rendered prompt would mean
storing the customer PII that redaction just worked to keep out of the model,
now sitting in a second place with a longer life than the request. The row keeps
what accounting and debugging need — which feature, which model, how many
tokens, what it cost, how long it took, and why it failed — and nothing that
would make it a liability in a database dump.

The running monthly sum of `cost_usd` per organization is what the cost ceiling
is enforced against, which is why `cost_usd` is `NUMERIC` and never a float: a
float that drifts by a rounding error per call drifts a whole tenant's budget
over a month of them.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin

#: A job's terminal states. `refused` is distinct from `failed`: the budget
#: stopped it before dispatch, so nothing was sent and nothing was spent — a
#: different fact from a call that reached the provider and errored.
AI_JOB_STATUSES = ("succeeded", "failed", "refused")


class AiJob(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "ai_jobs"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: Who triggered it. NULL for a background job with no user behind it — a
    #: nightly lead-scoring sweep is nobody's click, and faking an actor is how
    #: an audit trail starts lying.
    user_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: Which capability spent this — `assistant`, `lead_scoring`, `deal_health`.
    #: A short stable slug, so cost can be attributed per feature without parsing
    #: anything.
    feature: Mapped[str] = mapped_column(String(60), nullable=False)
    #: The prompt key and version that produced the request, when there was one.
    #: Lets a wording change be correlated with a shift in output quality.
    prompt_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    prompt_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False)

    prompt_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    #: USD, to the micro-dollar. NUMERIC because it is summed against a ceiling.
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, server_default="0"
    )

    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Why it failed or was refused, in words an operator can act on. NULL on
    #: success. Never carries the prompt or the answer — see the module
    #: docstring.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('succeeded', 'failed', 'refused')", name="ck_ai_jobs_status"
        ),
        CheckConstraint("length(feature) > 0", name="ck_ai_jobs_feature"),
        # The cost-ceiling query: this org's spend over a time window. Cost is in
        # the index so the running sum is answered from the index alone, without
        # touching the rows — it runs before every dispatch and must stay cheap.
        Index(
            "ix_ai_jobs_spend",
            "organization_id",
            "created_at",
            "cost_usd",
        ),
        # The admin/usage view: this org's jobs, newest first, filterable by
        # feature.
        Index("ix_ai_jobs_feature", "organization_id", "feature", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<AiJob {self.feature} {self.model} {self.status} ${self.cost_usd}>"
