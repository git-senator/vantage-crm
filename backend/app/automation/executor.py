"""The interpreter: walks a definition, one node at a time.

Execution is a loop over nodes with a hard step budget, not recursion, and it
**persists after every node**. Both matter:

* A workflow that fails at step six must not re-run steps one to five on retry.
  The run's `current_node_id` is the resume point, and every completed step has
  a row, so a retry picks up where it stopped rather than emailing a client
  twice.
* The step budget is a second line of defence behind validation's cycle
  rejection. Validation should make a loop impossible; if a bug lets one
  through, the budget bounds the damage to something an operator can read in the
  log rather than an unbounded stream of outbound email.

**A delay stops the loop.** The node sets `resume_at`, the run goes `waiting`,
and the function returns — no worker held, no timer in memory. A sweep restarts
it. That is the only shape that survives "wait three days".

**Action failure is terminal for the run by default.** A workflow whose second
step failed should not carry on to the third: the third almost always assumes
the second happened. The run stops, the step records why, and the log says
which node. Actions that are genuinely optional are a per-node setting rather
than a global policy, because "keep going" is a decision about one step's
meaning, not about workflows in general.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.actions import ACTIONS
from app.automation.conditions import evaluate_group
from app.automation.context import ExecutionContext
from app.automation.definition import WorkflowDefinition
from app.automation.handlers import ActionFailedError, run_action
from app.automation.scheduling import parse_business_hours, resume_at
from app.core.logging import get_logger
from app.models.automation import WorkflowRun, WorkflowRunStep

logger = get_logger(__name__)

#: How many nodes one execution slice may run. Not a workflow-size limit —
#: validation caps that at 50 — but a runaway guard: with cycles rejected at
#: publish time, hitting this means a bug, and the run fails loudly.
MAX_STEPS_PER_SLICE = 100


@dataclass(frozen=True, slots=True)
class SliceResult:
    """What one call to `execute` decided."""

    status: str
    #: Set when the run parked on a delay.
    resume_at: datetime | None = None
    error: str | None = None


class WorkflowExecutor:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def execute(
        self,
        run: WorkflowRun,
        definition: WorkflowDefinition,
        context: ExecutionContext,
        *,
        now: datetime | None = None,
    ) -> SliceResult:
        """Run from `run.current_node_id` until the workflow ends or waits."""
        moment = now or datetime.now(UTC)
        node_id = run.current_node_id or definition.start_node

        if not node_id:
            return SliceResult(status="succeeded")

        for _ in range(MAX_STEPS_PER_SLICE):
            if node_id is None:
                run.current_node_id = None
                return SliceResult(status="succeeded")

            node = definition.node(node_id)
            if node is None:
                # A definition that passed validation cannot reach here, so
                # this is corruption rather than a user error. Fail visibly.
                return SliceResult(
                    status="failed",
                    error=f"Step '{node_id}' is missing from the workflow.",
                )

            match node.type:
                case "condition":
                    taken = evaluate_group(
                        list(node.comparisons),
                        node.mode,
                        record=context.record,
                        previous=context.previous,
                        actor_id=context.actor_id,
                        now=moment,
                    )
                    await self._record_step(
                        run, node, "succeeded", {"matched": taken}
                    )
                    node_id = node.on_true if taken else node.on_false

                case "delay":
                    minutes = int(node.config.get("minutes", 0))
                    wake = resume_at(
                        minutes=minutes,
                        business_hours=parse_business_hours(node.config),
                        now=moment,
                    )
                    await self._record_step(
                        run,
                        node,
                        "succeeded",
                        {"resume_at": wake.isoformat(), "minutes": minutes},
                    )
                    # Resume *after* the delay, at the node it points to — not
                    # at the delay itself, or waking would re-park immediately.
                    run.current_node_id = node.next
                    return SliceResult(status="waiting", resume_at=wake)

                case "action":
                    outcome = await self._run_action(node, context)
                    if outcome.failed:
                        await self._record_step(
                            run, node, "failed", outcome.output, outcome.error
                        )
                        if not node.config.get("continue_on_error"):
                            run.current_node_id = node.id
                            return SliceResult(
                                status="failed", error=outcome.error
                            )
                    else:
                        await self._record_step(
                            run, node, "succeeded", outcome.output
                        )
                        context.remember(node.id, outcome.output)
                    node_id = node.next

                case _:  # pragma: no cover — validation rejects this
                    return SliceResult(
                        status="failed",
                        error=f"Step '{node_id}' has an unknown type.",
                    )

            run.current_node_id = node_id
            run.context = context.variables
            await self.session.flush()

        logger.error(
            "workflow_step_budget_exhausted",
            extra={"run_id": str(run.id), "workflow_id": str(run.workflow_id)},
        )
        return SliceResult(
            status="failed",
            error="This workflow ran too many steps and was stopped.",
        )

    # ------------------------------------------------------------ actions

    @dataclass(frozen=True, slots=True)
    class _Outcome:
        failed: bool
        output: dict[str, Any]
        error: str | None = None

    async def _run_action(self, node: Any, context: ExecutionContext) -> _Outcome:
        definition = ACTIONS.get(node.action or "")
        if definition is None:  # pragma: no cover — validation rejects this
            return self._Outcome(True, {}, "Unknown action.")

        try:
            output = await run_action(
                self.session, definition, node.config, context
            )
        except ActionFailedError as exc:
            # A failure the action itself diagnosed: a missing email address, a
            # WhatsApp session window. The message is written for the person
            # reading the execution log, not for a developer.
            logger.warning(
                "workflow_action_failed",
                extra={
                    "run_id": str(context.run_id),
                    "action": definition.key,
                    "reason": str(exc),
                },
            )
            return self._Outcome(True, {}, str(exc)[:1000])
        except Exception:
            # An unexpected fault. The run fails rather than continuing into a
            # step that assumes this one worked. The message stays generic —
            # an internal error's text is not written for a workflow author —
            # and the trace goes to the log.
            logger.exception(
                "workflow_action_errored",
                extra={"run_id": str(context.run_id), "action": definition.key},
            )
            return self._Outcome(
                True, {}, f"{definition.label} could not be completed."
            )

        if definition.external:
            # Louder for anything that leaves the building: an automation bug
            # that files a task is embarrassing, one that emails four hundred
            # clients is a different category of incident.
            logger.info(
                "workflow_external_action",
                extra={
                    "run_id": str(context.run_id),
                    "workflow_id": str(context.workflow_id),
                    "action": definition.key,
                },
            )
        return self._Outcome(False, output)

    # --------------------------------------------------------------- log

    async def _record_step(
        self,
        run: WorkflowRun,
        node: Any,
        status: str,
        output: dict[str, Any],
        error: str | None = None,
    ) -> None:
        """Append to the execution log.

        The node's label is copied in rather than referenced, so editing the
        workflow afterwards cannot rewrite what the log says happened.
        """
        step = WorkflowRunStep(
            organization_id=run.organization_id,
            run_id=run.id,
            sequence=await self._next_sequence(run),
            node_id=node.id,
            node_type=node.type,
            node_label=node.label,
            status=status,
            output=output,
            error=error[:1000] if error else None,
            finished_at=datetime.now(UTC),
        )
        self.session.add(step)
        await self.session.flush()

    async def _next_sequence(self, run: WorkflowRun) -> int:
        """Next position in the log.

        Counted from the database rather than an in-memory counter because a
        run resumes in a different process than it started in, and two slices
        both starting at zero would collide on the unique constraint.
        """
        from sqlalchemy import func, select

        current = (
            await self.session.execute(
                select(func.coalesce(func.max(WorkflowRunStep.sequence), 0)).where(
                    WorkflowRunStep.run_id == run.id
                )
            )
        ).scalar_one()
        return int(current) + 1
