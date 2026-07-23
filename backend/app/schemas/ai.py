"""AI infrastructure contracts.

Cost figures cross the wire as **strings**, the same rule the analytics and
reporting contracts follow: a budget is money, and money that has been through a
JSON float has lost the precision the NUMERIC column exists to protect.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class AiStatus(BaseModel):
    """Whether the AI layer is available to this caller.

    The frontend reads this to decide whether to show AI affordances at all —
    an assistant button on a workspace with the layer switched off is a dead
    end, and one shown to a user without `ai.use` is a permission error waiting
    to happen.
    """

    enabled: bool
    provider: str
    model: str
    can_use: bool
    can_configure: bool


class AiBudget(BaseModel):
    ceiling_usd: Decimal
    spent_usd: Decimal
    #: Null when the ceiling is disabled — "unlimited", not zero remaining.
    remaining_usd: Decimal | None
    #: True once spend has reached the ceiling; further calls are refused before
    #: dispatch.
    exhausted: bool


class AiUsageRow(BaseModel):
    feature: str
    calls: int
    cost_usd: Decimal


class AiStatusResponse(BaseModel):
    status: AiStatus
    budget: AiBudget
