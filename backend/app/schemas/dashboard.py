"""Dashboard contracts.

The dashboard is a set of aggregates computed **within the caller's scope** — an
agent's dashboard reports the agent's book, a manager's the team's. Every count
and sum here is the same scope predicate the entity's own list endpoint applies,
so the numbers can never exceed what the user could reach by browsing.

Money is `Decimal`, serialised as a string by Pydantic — the same choice the
deal schema makes, so a forecast total never round-trips through a JS float.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from app.schemas.timeline import TimelineItem


class LeadStats(BaseModel):
    open: int
    total: int


class ClientStats(BaseModel):
    total: int


class PropertyStats(BaseModel):
    active: int
    total: int


class DealStats(BaseModel):
    open_count: int
    #: Sum of `value` across open deals — the raw pipeline.
    open_value: Decimal
    #: Sum of `value * probability` across open deals — the weighted forecast.
    weighted_value: Decimal
    won_this_month_count: int
    won_this_month_value: Decimal


class TaskStats(BaseModel):
    open: int
    overdue: int


class DashboardSummary(BaseModel):
    leads: LeadStats
    clients: ClientStats
    properties: PropertyStats
    deals: DealStats
    tasks: TaskStats
    #: The merged activity+note feed, within scope — the "recent activity" panel.
    recent_activity: list[TimelineItem]
