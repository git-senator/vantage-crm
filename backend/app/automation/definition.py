"""The workflow definition: parsing, validation, and why it is a tree.

A definition is one trigger and a graph of nodes:

```json
{
  "trigger": {"type": "lead.created", "config": {}},
  "start_node": "n1",
  "nodes": {
    "n1": {"type": "condition", "config": {"mode": "all"},
           "comparisons": [...], "on_true": "n2", "on_false": null},
    "n2": {"type": "action", "action": "create_task", "config": {...},
           "next": null}
  }
}
```

**Why a tree, not a DAG.** Nodes have at most one outgoing edge each (two for a
condition), and no node may be reached twice. That rules out joins — "wait for
both branches, then continue" — which is the one thing a DAG buys and the thing
that brings the rest of the workflow engine's complexity with it: partial state,
join timeouts, and a run that is half-finished in two places at once. Every CRM
automation builder people actually use makes the same call, and the escape hatch
when somebody needs a join is a second workflow triggered by the first one's
side effect.

**Why cycles are rejected rather than bounded.** A loop with a step budget looks
like a feature until a workflow emails a client forty times because the budget
was fifty. Validation refuses a cycle at publish time, where it is a message in
the builder rather than an incident.

Validation runs on publish, not on save. A draft is allowed to be incoherent —
that is what a draft is — and blocking every save on a complete definition makes
the builder unusable while you are halfway through building.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.automation.actions import ACTIONS
from app.automation.conditions import GROUP_MODES, OPERATORS, Comparison
from app.automation.triggers import TRIGGERS

#: Node kinds. `delay` is its own kind rather than an action because it is the
#: only one that suspends the run, and the executor branches on that rather
#: than on a magic action key.
NODE_TYPES = ("action", "condition", "delay")

#: A definition larger than this is a sign the builder should be two workflows.
#: The limit exists to bound execution and storage, not to be a design opinion —
#: but it is also the point at which nobody can read the thing on a screen.
MAX_NODES = 50

#: Longest a single delay may park a run. Beyond a month, "wait then act" stops
#: being an automation and starts being a scheduled report.
MAX_DELAY_MINUTES = 60 * 24 * 30


class DefinitionError(ValueError):
    """A definition that cannot be published. Carries every problem found."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True, slots=True)
class Node:
    id: str
    type: str
    label: str | None = None
    #: `action` nodes only.
    action: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    #: `condition` nodes only.
    comparisons: tuple[Comparison, ...] = ()
    mode: str = "all"
    on_true: str | None = None
    on_false: str | None = None
    #: Every other node kind.
    next: str | None = None

    def successors(self) -> list[str]:
        if self.type == "condition":
            return [target for target in (self.on_true, self.on_false) if target]
        return [self.next] if self.next else []


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    trigger_type: str
    trigger_config: dict[str, Any]
    start_node: str | None
    nodes: dict[str, Node]

    def node(self, node_id: str) -> Node | None:
        return self.nodes.get(node_id)


def parse(raw: dict[str, Any]) -> WorkflowDefinition:
    """Structure a stored definition. Shape errors only — see `validate`.

    Tolerant on purpose: a draft is saved as whatever the builder had, so
    parsing must not throw on a half-configured node. Anything genuinely
    unusable is caught by `validate` before it can be published.
    """
    trigger = raw.get("trigger") or {}
    nodes: dict[str, Node] = {}

    for node_id, body in (raw.get("nodes") or {}).items():
        if not isinstance(body, dict):
            continue
        comparisons = tuple(
            Comparison(
                field=str(item.get("field", "")),
                operator=str(item.get("operator", "")),
                value=item.get("value"),
            )
            for item in (body.get("comparisons") or [])
            if isinstance(item, dict)
        )
        nodes[str(node_id)] = Node(
            id=str(node_id),
            type=str(body.get("type", "")),
            label=body.get("label"),
            action=body.get("action"),
            config=body.get("config") or {},
            comparisons=comparisons,
            mode=str(body.get("mode", "all")),
            on_true=body.get("on_true"),
            on_false=body.get("on_false"),
            next=body.get("next"),
        )

    return WorkflowDefinition(
        trigger_type=str(trigger.get("type", "")),
        trigger_config=trigger.get("config") or {},
        start_node=raw.get("start_node"),
        nodes=nodes,
    )


def validate(definition: WorkflowDefinition) -> list[str]:
    """Every reason this definition cannot be published.

    Returns all of them rather than the first, so the builder can mark up the
    whole canvas in one pass instead of making the author fix, save, discover,
    repeat.
    """
    errors: list[str] = []

    trigger = TRIGGERS.get(definition.trigger_type)
    if trigger is None:
        errors.append(
            f"Unknown trigger '{definition.trigger_type}'."
            if definition.trigger_type
            else "A workflow needs a trigger."
        )
    else:
        errors.extend(trigger.validate_config(definition.trigger_config))

    if not definition.nodes:
        errors.append("A workflow needs at least one step.")
    if len(definition.nodes) > MAX_NODES:
        errors.append(f"A workflow may have at most {MAX_NODES} steps.")
    if definition.start_node and definition.start_node not in definition.nodes:
        errors.append("The first step points at a step that does not exist.")
    if definition.nodes and not definition.start_node:
        errors.append("A workflow needs a first step.")

    entity_type = trigger.entity_type if trigger else ""

    for node in definition.nodes.values():
        errors.extend(_validate_node(node, entity_type, definition))

    errors.extend(_validate_reachability(definition))

    return errors


def _validate_node(
    node: Node, entity_type: str, definition: WorkflowDefinition
) -> list[str]:
    errors: list[str] = []
    where = node.label or node.id

    if node.type not in NODE_TYPES:
        return [f"Step '{where}' has an unknown type."]

    for target in node.successors():
        if target not in definition.nodes:
            errors.append(f"Step '{where}' points at a step that does not exist.")

    match node.type:
        case "action":
            action = ACTIONS.get(node.action or "")
            if action is None:
                errors.append(f"Step '{where}' uses an unknown action.")
            else:
                errors.extend(
                    f"{where}: {problem}"
                    for problem in action.validate_config(node.config)
                )
                if (
                    action.entity_types
                    and entity_type
                    and entity_type not in action.entity_types
                ):
                    # Caught here rather than at run time, where it would be a
                    # failed run for every record that ever triggers it.
                    errors.append(
                        f"Step '{where}' cannot run on a {entity_type} trigger."
                    )

        case "condition":
            if node.mode not in GROUP_MODES:
                errors.append(f"Step '{where}' has an invalid match mode.")
            if not node.comparisons:
                errors.append(f"Step '{where}' has no conditions to check.")
            if not node.on_true and not node.on_false:
                errors.append(f"Step '{where}' has no branches.")
            for comparison in node.comparisons:
                operator = OPERATORS.get(comparison.operator)
                if operator is None:
                    errors.append(
                        f"Step '{where}' uses an unknown comparison "
                        f"'{comparison.operator}'."
                    )
                    continue
                if not comparison.field:
                    errors.append(f"Step '{where}' has a condition with no field.")
                if operator.takes_value and comparison.value in (None, ""):
                    errors.append(
                        f"Step '{where}': '{operator.label}' needs a value."
                    )

        case "delay":
            minutes = node.config.get("minutes")
            if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes < 1:
                errors.append(f"Step '{where}' needs a delay of at least one minute.")
            elif minutes > MAX_DELAY_MINUTES:
                errors.append(
                    f"Step '{where}' waits longer than the 30-day maximum."
                )
            if not node.next:
                errors.append(f"Step '{where}' waits and then does nothing.")

    return errors


def _validate_reachability(definition: WorkflowDefinition) -> list[str]:
    """Reject cycles and orphans.

    Cycles are refused rather than bounded by a step budget: a loop that emails
    a client forty times because the budget was fifty is not a smaller bug than
    one that never stops.

    Orphans are an error rather than a warning because a step nobody can reach
    is almost always a wiring mistake, and silently ignoring it means the author
    believes their workflow does something it does not.
    """
    if not definition.start_node or definition.start_node not in definition.nodes:
        return []

    errors: list[str] = []
    seen: set[str] = set()
    stack: list[tuple[str, tuple[str, ...]]] = [(definition.start_node, ())]

    while stack:
        node_id, path = stack.pop()
        if node_id in path:
            node = definition.nodes.get(node_id)
            errors.append(
                f"Step '{node.label or node_id if node else node_id}' loops back "
                f"on itself. Workflows cannot contain loops."
            )
            continue
        if node_id in seen:
            # Two branches converging. Harmless — both simply continue into the
            # same tail — and not worth forbidding just to keep the shape a
            # strict tree.
            continue
        seen.add(node_id)

        node = definition.nodes.get(node_id)
        if node is None:
            continue
        for target in node.successors():
            stack.append((target, (*path, node_id)))

    orphans = sorted(set(definition.nodes) - seen)
    errors.extend(
        f"Step '{definition.nodes[node_id].label or node_id}' cannot be reached."
        for node_id in orphans
    )
    return errors


def validate_or_raise(raw: dict[str, Any]) -> WorkflowDefinition:
    definition = parse(raw)
    errors = validate(definition)
    if errors:
        raise DefinitionError(errors)
    return definition
