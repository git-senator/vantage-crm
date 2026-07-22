"""Dashboard business logic.

Assembles the summary by resolving each entity's *own* view scope and passing it
to the aggregate query. The scope a user holds on leads decides which leads are
counted; the scope on deals decides which deals; and so on — there is no
separate "dashboard scope", because that would be a second, drifting definition
of what a user can see. An entity the caller cannot view at all contributes
zero, not an error: a dashboard is a composite, and a missing grant simply
blanks a panel.

`recent_activity` reuses the timeline feed, so the panel and the timeline agree
by construction.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.dashboard import DashboardRepository
from app.schemas.dashboard import (
    ClientStats,
    DashboardSummary,
    DealStats,
    LeadStats,
    PropertyStats,
    TaskStats,
)
from app.services.rbac import AuthorizationContext, RbacService
from app.services.timeline import TimelineService

RECENT_ACTIVITY_LIMIT = 15


class DashboardService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.repo = DashboardRepository(session)
        self.rbac = RbacService(session)
        self.timeline = TimelineService(session, auth)

    async def _scope_ids(self, permission: str):  # type: ignore[no-untyped-def]
        """Owner/assignee ids the caller's grant on `permission` permits.

        Returns the sentinel `False` when the caller holds no grant at all, so
        the caller can distinguish "everyone" (None) from "no access" (False).
        """
        scope = self.auth.scope_for(permission)
        if scope is None:
            return False
        return await self.rbac.owner_ids_for_scope(self.auth, scope)

    async def summary(self) -> DashboardSummary:
        org = self.auth.organization_id
        now = datetime.now(UTC)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        lead_scope = await self._scope_ids("leads.view")
        if lead_scope is False:
            leads = LeadStats(open=0, total=0)
        else:
            open_, total = await self.repo.lead_counts(org, lead_scope)
            leads = LeadStats(open=open_, total=total)

        client_scope = await self._scope_ids("contacts.view")
        if client_scope is False:
            clients = ClientStats(total=0)
        else:
            clients = ClientStats(total=await self.repo.client_count(org, client_scope))

        property_scope = await self._scope_ids("properties.view")
        if property_scope is False:
            properties = PropertyStats(active=0, total=0)
        else:
            active, total = await self.repo.property_counts(org, property_scope)
            properties = PropertyStats(active=active, total=total)

        deal_scope = await self._scope_ids("deals.view")
        if deal_scope is False:
            deals = DealStats(
                open_count=0,
                open_value=Decimal(0),
                weighted_value=Decimal(0),
                won_this_month_count=0,
                won_this_month_value=Decimal(0),
            )
        else:
            stats = await self.repo.deal_stats(org, deal_scope, month_start=month_start)
            deals = DealStats(**stats)

        task_scope = await self._scope_ids("tasks.view")
        if task_scope is False:
            tasks = TaskStats(open=0, overdue=0)
        else:
            open_tasks, overdue = await self.repo.task_counts(org, task_scope, now=now)
            tasks = TaskStats(open=open_tasks, overdue=overdue)

        recent = await self.timeline.feed(limit=RECENT_ACTIVITY_LIMIT)

        return DashboardSummary(
            leads=leads,
            clients=clients,
            properties=properties,
            deals=deals,
            tasks=tasks,
            recent_activity=recent,
        )
