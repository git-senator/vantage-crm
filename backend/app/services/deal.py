"""Deal business logic.

Follows the LeadService pattern for CRUD. Two things here are genuinely new and
are the reason Deals is not another CRUD module:

**Stage transitions are a domain action.** `move_stage` writes a history row,
recalculates probability, sets or clears the close date, emits an activity and
audits — atomically. Exposing `stage_id` on a PATCH would let a client do the
first part without any of the rest, and the analytics substrate would quietly
develop holes.

**Commission has a rule.** `commission_amount` is authoritative. When a rate is
supplied and an amount is not, the amount is computed once at write time and
then left alone. Flat fees and negotiated splits are real, so a stored amount
is never silently recomputed out from under the person who agreed it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from app.core.logging import get_logger
from app.core.permissions import Scope
from app.models.activity import Activity
from app.models.deal import Deal, DealStageHistory
from app.models.pipeline import Pipeline, PipelineStage
from app.models.user import User
from app.repositories.client import ClientRepository
from app.repositories.deal import DealRepository, DealStageHistoryRepository
from app.repositories.pipeline import PipelineRepository
from app.repositories.property import PropertyRepository
from app.repositories.user import UserRepository
from app.schemas.common import Cursor
from app.schemas.deal import (
    DealCreate,
    DealFilters,
    DealStageTransition,
    DealUpdate,
)
from app.services.activity import ActivityService
from app.services.audit import AuditService, build_diff
from app.services.rbac import AuthorizationContext, RbacService

logger = get_logger(__name__)

ENTITY_TYPE = "deal"

AUDITED_FIELDS = (
    "title",
    "client_id",
    "property_id",
    "value",
    "currency",
    "commission_amount",
    "commission_rate",
    "probability",
    "priority",
    "expected_close_date",
    "owner_id",
)


def compute_commission(
    value: Decimal | None, rate: Decimal | None
) -> Decimal | None:
    """value x rate, rounded to cents.

    ROUND_HALF_UP rather than Python's banker's rounding: this is money someone
    is paid, and the convention every brokerage's accounting expects is
    half-up. Banker's rounding here would produce cheques that disagree with
    the contract by a cent, which is a support ticket every time.
    """
    if value is None or rate is None:
        return None
    return (value * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class DealService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.deals = DealRepository(session)
        self.history = DealStageHistoryRepository(session)
        self.pipelines = PipelineRepository(session)
        self.clients = ClientRepository(session)
        self.properties = PropertyRepository(session)
        self.users = UserRepository(session)
        self.audit = AuditService(session)
        self.activities = ActivityService(session)
        self.rbac = RbacService(session)

    # -------------------------------------------------------------- scope

    async def _owner_ids(self, permission: str) -> list[UUID] | None:
        scope = self.auth.require(permission)
        return await self.rbac.owner_ids_for_scope(self.auth, scope)

    # --------------------------------------------------------------- read

    async def list_deals(
        self, *, filters: DealFilters, limit: int, cursor: Cursor | None
    ) -> tuple[list[Deal], bool]:
        owner_ids = await self._owner_ids("deals.view")
        return await self.deals.list_page(
            self.auth.organization_id,
            owner_ids=owner_ids,
            filters=filters,
            limit=limit,
            cursor=cursor,
        )

    async def get_deal(self, deal_id: UUID) -> Deal:
        owner_ids = await self._owner_ids("deals.view")
        deal = await self.deals.get_visible(
            deal_id, self.auth.organization_id, owner_ids
        )
        if deal is None:
            # 404, not 403 — a deal is a personal book of business, unlike a
            # property listing, so its existence is not public within the org.
            raise NotFoundError("Deal not found.")
        return deal

    async def board(
        self, *, pipeline_id: UUID | None, filters: DealFilters
    ) -> tuple[Pipeline, list[Deal]]:
        """Every visible deal in one pipeline, plus the pipeline itself."""
        owner_ids = await self._owner_ids("deals.view")

        if pipeline_id is None:
            pipeline = await self.pipelines.get_default(self.auth.organization_id)
        else:
            pipeline = await self.pipelines.get_with_stages(
                pipeline_id, self.auth.organization_id
            )
        if pipeline is None:
            raise NotFoundError("Pipeline not found.")

        deals = await self.deals.list_for_board(
            self.auth.organization_id,
            owner_ids=owner_ids,
            pipeline_id=pipeline.id,
            filters=filters,
        )
        return pipeline, deals

    async def stage_history(self, deal_id: UUID) -> list[DealStageHistory]:
        # Resolved through get_deal, so history cannot become a way to read a
        # deal the caller could not fetch directly.
        deal = await self.get_deal(deal_id)
        return await self.history.list_for_deal(deal.id, self.auth.organization_id)

    async def timeline(self, deal_id: UUID) -> list[Activity]:
        # `get_deal` has already resolved the deal under RBAC, so the timeline
        # read uses the *unchecked* path deliberately — re-running the parent
        # check would need an auth-bearing ActivityService this one is not.
        deal = await self.get_deal(deal_id)
        return await self.activities.list_for_entity_unchecked(
            organization_id=self.auth.organization_id,
            entity_type=ENTITY_TYPE,
            entity_id=deal.id,
        )

    # -------------------------------------------------------------- write

    async def create_deal(self, payload: DealCreate, actor: User) -> Deal:
        self.auth.require("deals.manage")

        owner_id = payload.owner_id or actor.id
        if owner_id != actor.id:
            await self._assert_can_assign_to(owner_id)

        await self._assert_client_exists(payload.client_id)
        if payload.property_id is not None:
            await self._assert_property_exists(payload.property_id)

        pipeline, stage = await self._resolve_entry_stage(
            payload.pipeline_id, payload.stage_id
        )

        probability = (
            payload.probability
            if payload.probability is not None
            else stage.default_probability
        )

        data = payload.model_dump(
            exclude={
                "owner_id",
                "pipeline_id",
                "stage_id",
                "probability",
                "commission_amount",
            }
        )
        deal = Deal(
            organization_id=self.auth.organization_id,
            owner_id=owner_id,
            pipeline_id=pipeline.id,
            stage_id=stage.id,
            probability=probability,
            created_by=actor.id,
            updated_by=actor.id,
            **data,
        )
        # Amount wins when given; otherwise derive it from the rate once.
        deal.commission_amount = payload.commission_amount or compute_commission(
            payload.value, payload.commission_rate
        )
        # A deal created directly into a terminal stage is closed today.
        if stage.is_won or stage.is_lost:
            deal.actual_close_date = date.today()

        self.session.add(deal)
        await self.session.flush()

        # The opening history row. Without it the first transition has no
        # baseline to measure time-in-stage from, and every deal's first
        # duration would be null.
        self.session.add(
            DealStageHistory(
                organization_id=self.auth.organization_id,
                deal_id=deal.id,
                from_stage_id=None,
                to_stage_id=stage.id,
                changed_by=actor.id,
            )
        )
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_CREATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=deal.id,
            metadata={"stage": stage.key, "value": str(deal.value or "")},
        )
        logger.info("deal_created", extra={"deal_id": str(deal.id)})
        return await self.get_deal(deal.id)

    async def update_deal(
        self, deal_id: UUID, payload: DealUpdate, actor: User
    ) -> Deal:
        owner_ids = await self._owner_ids("deals.manage")
        deal = await self.deals.get_visible(
            deal_id, self.auth.organization_id, owner_ids
        )
        if deal is None:
            raise NotFoundError("Deal not found.")

        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            return deal

        if "owner_id" in updates and updates["owner_id"] != deal.owner_id:
            await self._assert_can_assign_to(updates["owner_id"])
        if "client_id" in updates and updates["client_id"] is not None:
            await self._assert_client_exists(updates["client_id"])
        if updates.get("property_id") is not None:
            await self._assert_property_exists(updates["property_id"])

        before = {field: getattr(deal, field) for field in AUDITED_FIELDS}
        for field, value in updates.items():
            setattr(deal, field, value)

        # Recompute only when the rate changed and no explicit amount came with
        # it. An amount already on the record is somebody's agreed figure.
        if "commission_rate" in updates and "commission_amount" not in updates:
            deal.commission_amount = compute_commission(
                deal.value, deal.commission_rate
            )

        deal.updated_by = actor.id
        await self.session.flush()

        after = {field: getattr(deal, field) for field in AUDITED_FIELDS}
        diff = build_diff(before, after)

        if diff:
            await self.audit.record(
                action=AuditAction.RECORD_UPDATED,
                organization_id=self.auth.organization_id,
                actor_id=actor.id,
                actor_email=actor.email,
                entity_type=ENTITY_TYPE,
                entity_id=deal.id,
                metadata={"changes": diff},
            )
            logger.info(
                "deal_updated",
                extra={"deal_id": str(deal.id), "fields": sorted(diff)},
            )
        return await self.get_deal(deal.id)

    async def delete_deal(self, deal_id: UUID, actor: User) -> None:
        """Soft delete. The row, its history and its audit trail survive."""
        owner_ids = await self._owner_ids("deals.manage")
        deal = await self.deals.get_visible(
            deal_id, self.auth.organization_id, owner_ids
        )
        if deal is None:
            raise NotFoundError("Deal not found.")

        deal.deleted_at = datetime.now(UTC)
        deal.updated_by = actor.id
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_DELETED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=deal.id,
            metadata={"title": deal.title, "value": str(deal.value or "")},
        )
        logger.info("deal_deleted", extra={"deal_id": str(deal.id)})

    async def assign_deal(self, deal_id: UUID, owner_id: UUID, actor: User) -> Deal:
        """Reassign ownership. Gated on `deals.manage` plus assignment scope."""
        self.auth.require("deals.manage")
        await self._assert_can_assign_to(owner_id)

        owner_ids = await self._owner_ids("deals.manage")
        deal = await self.deals.get_visible(
            deal_id, self.auth.organization_id, owner_ids
        )
        if deal is None:
            raise NotFoundError("Deal not found.")

        previous = deal.owner_id
        deal.owner_id = owner_id
        deal.updated_by = actor.id
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_UPDATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=deal.id,
            metadata={
                "changes": {
                    "owner_id": {
                        "old": str(previous) if previous else None,
                        "new": str(owner_id),
                    }
                }
            },
        )
        return await self.get_deal(deal.id)

    # ---------------------------------------------------------- transition

    async def move_stage(
        self, deal_id: UUID, payload: DealStageTransition, actor: User
    ) -> Deal:
        """Move a deal to another stage.

        The domain action this whole module exists for. In one transaction:

          1. lock the deal, so two concurrent drags of the same card cannot
             both compute a duration from the same baseline;
          2. write the history row, with time spent in the stage being left;
          3. reset probability to the new stage's default, unless overridden;
          4. set `actual_close_date` entering a terminal stage, clear it on
             reopen — a reopened deal that keeps its close date is a deal that
             reports as closed forever;
          5. emit a `stage_change` activity and an audit entry.

        Moving into a losing stage requires `lost_reason`. A lost deal with no
        reason is the single most useless row in a CRM, because "why do we lose
        deals" is the question the pipeline exists to answer.
        """
        owner_ids = await self._owner_ids("deals.manage")
        deal = await self.deals.get_visible_for_update(
            deal_id, self.auth.organization_id, owner_ids
        )
        if deal is None:
            raise NotFoundError("Deal not found.")

        target = await self.pipelines.get_stage(
            payload.to_stage_id, self.auth.organization_id
        )
        if target is None:
            raise NotFoundError("Stage not found.")
        if target.pipeline_id != deal.pipeline_id:
            # Silently re-homing a deal into another pipeline would corrupt
            # both funnels' analytics.
            raise ConflictError(
                "That stage belongs to a different pipeline."
            )
        if target.id == deal.stage_id:
            raise ConflictError("The deal is already in that stage.")

        if target.is_lost and not (payload.lost_reason or "").strip():
            raise ConflictError(
                "A reason is required when marking a deal lost."
            )

        previous_stage_id = deal.stage_id
        previous = await self.pipelines.get_stage(
            previous_stage_id, self.auth.organization_id
        )

        # Time spent in the stage being left, measured from the last
        # transition. Stored on the row so cycle-time analytics is a column
        # read rather than a window function over the whole table.
        last = await self.history.latest_for_deal(deal.id, self.auth.organization_id)
        now = datetime.now(UTC)
        duration = (now - last.changed_at) if last is not None else None

        deal.stage_id = target.id
        deal.probability = (
            payload.probability
            if payload.probability is not None
            else target.default_probability
        )
        if target.is_won or target.is_lost:
            deal.actual_close_date = date.today()
            deal.lost_reason = payload.lost_reason if target.is_lost else None
        else:
            # Reopened. Clearing both is what stops a revived deal reporting as
            # closed forever.
            deal.actual_close_date = None
            deal.lost_reason = None
        deal.updated_by = actor.id

        self.session.add(
            DealStageHistory(
                organization_id=self.auth.organization_id,
                deal_id=deal.id,
                from_stage_id=previous_stage_id,
                to_stage_id=target.id,
                changed_by=actor.id,
                changed_at=now,
                duration_in_stage=duration,
                note=payload.note,
            )
        )
        await self.session.flush()

        from_name = previous.name if previous else "—"
        await self.activities.record(
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            entity_type=ENTITY_TYPE,
            entity_id=deal.id,
            type="stage_change",
            subject=f"Moved from {from_name} to {target.name}",
            body=payload.note,
            occurred_at=now,
            metadata={
                "from_stage": previous.key if previous else None,
                "to_stage": target.key,
                "duration_seconds": duration.total_seconds() if duration else None,
            },
        )
        await self.audit.record(
            action=AuditAction.RECORD_STAGE_CHANGED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=deal.id,
            metadata={
                "from_stage": previous.key if previous else None,
                "to_stage": target.key,
            },
        )
        logger.info(
            "deal_stage_changed",
            extra={
                "deal_id": str(deal.id),
                "from": previous.key if previous else None,
                "to": target.key,
            },
        )
        return await self.get_deal(deal.id)

    # ------------------------------------------------------------ helpers

    async def _resolve_entry_stage(
        self, pipeline_id: UUID | None, stage_id: UUID | None
    ) -> tuple[Pipeline, PipelineStage]:
        """Work out which pipeline and stage a new deal lands in."""
        if stage_id is not None:
            stage = await self.pipelines.get_stage(stage_id, self.auth.organization_id)
            if stage is None:
                raise NotFoundError("Stage not found.")
            if pipeline_id is not None and stage.pipeline_id != pipeline_id:
                raise ConflictError("That stage belongs to a different pipeline.")
            pipeline = await self.pipelines.get_with_stages(
                stage.pipeline_id, self.auth.organization_id
            )
            if pipeline is None:
                raise NotFoundError("Pipeline not found.")
            return pipeline, stage

        if pipeline_id is not None:
            pipeline = await self.pipelines.get_with_stages(
                pipeline_id, self.auth.organization_id
            )
        else:
            pipeline = await self.pipelines.get_default(self.auth.organization_id)
        if pipeline is None:
            raise ConflictError(
                "This workspace has no pipeline configured. "
                "An administrator must create one before deals can be added."
            )

        stage = await self.pipelines.first_stage(
            pipeline.id, self.auth.organization_id
        )
        if stage is None:
            raise ConflictError(
                f"Pipeline '{pipeline.name}' has no stages. "
                "An administrator must add one before deals can be added."
            )
        return pipeline, stage

    async def _assert_client_exists(self, client_id: UUID) -> None:
        """A deal without a client is a note, not a deal.

        Checked against the tenant rather than the caller's contact scope: an
        agent may legitimately open a deal against a colleague's client, and
        requiring `contacts.view` here would couple the two permissions.
        """
        client = await self.clients.get(client_id, self.auth.organization_id)
        if client is None:
            raise NotFoundError("Client not found in this workspace.")

    async def _assert_property_exists(self, property_id: UUID) -> None:
        listing = await self.properties.get(property_id, self.auth.organization_id)
        if listing is None:
            raise NotFoundError("Property not found in this workspace.")

    async def _assert_can_assign_to(self, owner_id: UUID) -> None:
        """The assignee must be a real member of this workspace.

        Without this an id from another tenant could be written into
        `owner_id`. RLS would then hide the deal from everyone, because no user
        in this organization matches — data silently lost rather than leaked,
        but lost all the same.
        """
        target = await self.users.get(owner_id, self.auth.organization_id)
        if target is None:
            raise NotFoundError("Assignee is not a member of this workspace.")

        # Deals have no separate `assign` permission — reassigning one is part
        # of managing it. The *scope* of deals.manage is what limits who you
        # can hand work to.
        scope = self.auth.scope_for("deals.manage")
        if scope is None:
            raise PermissionDeniedError(
                "This action requires the 'deals.manage' permission."
            )
        if scope is Scope.OWN and owner_id != self.auth.user_id:
            raise PermissionDeniedError("You can only assign deals to yourself.")
        if scope is Scope.TEAM:
            teammates = await self.rbac.team_member_ids(
                self.auth.user_id, self.auth.organization_id
            )
            if owner_id not in teammates:
                raise PermissionDeniedError(
                    "You can only assign deals within your team."
                )
