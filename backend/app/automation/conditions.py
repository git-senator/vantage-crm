"""Conditions — the branch points in a workflow.

A condition node holds a small boolean tree: a group of comparisons combined
with ALL or ANY, one level deep. Not an arbitrary nested expression, and not a
string language.

**Why not a custom expression language.** A CRM automation builder that accepts
`lead.budget > 500000 and lead.tags contains "cash"` needs a parser, a
sandbox, and an answer for what happens when the field does not exist. Every one
of those is a place for a workflow to do something its author did not intend,
and the third one is genuinely hard: an absent field is neither greater nor less
than anything. A structured comparison list gives the builder something it can
render, gives validation something it can check against the field registry, and
has exactly one behaviour for a missing field.

**Why one level of nesting.** `(A and B) or (C and D)` covers what people
actually build. Full nesting means a recursive UI, and the escape hatch when
somebody genuinely needs it is a second workflow.

The comparison semantics that matter:

* **A missing field never matches.** Not "matches empty", not an error that
  fails the run — it evaluates false, and the step output says which field was
  absent. A run that dies because a lead has no phone number is worse than one
  that skips the branch and says so.
* **Comparisons are typed by the operator, not the value.** `greater_than` on
  two strings that look like numbers coerces both; on anything else it is false.
  Guessing from the values means `"10" > "9"` is false for a string comparison
  and true for a numeric one, which is a bug nobody finds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from app.automation.registry import Definition, FieldOption, FieldSpec, build_registry
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class OperatorDefinition(Definition):
    """A comparison. `takes_value` is false for the unary ones."""

    takes_value: bool = True
    #: Which widget the builder should offer for the right-hand side.
    value_kind: str = "text"


def _operator(
    key: str,
    label: str,
    description: str,
    category: str,
    *,
    takes_value: bool = True,
    value_kind: str = "text",
) -> OperatorDefinition:
    return OperatorDefinition(
        key=key,
        label=label,
        description=description,
        category=category,
        takes_value=takes_value,
        value_kind=value_kind,
    )


OPERATORS = build_registry(
    # --------------------------------------------------------- equality
    _operator("equals", "is", "Exactly equal, compared as text.", "Basic"),
    _operator("not_equals", "is not", "Not equal.", "Basic"),
    _operator("is_set", "is set", "Has any value.", "Basic", takes_value=False),
    _operator("is_empty", "is empty", "Has no value.", "Basic", takes_value=False),
    # ------------------------------------------------------------- text
    _operator("contains", "contains", "Case-insensitive substring.", "Text"),
    _operator("not_contains", "does not contain", "The inverse.", "Text"),
    _operator("starts_with", "starts with", "Case-insensitive prefix.", "Text"),
    _operator("in_list", "is one of", "Matches any comma-separated value.", "Text"),
    # ----------------------------------------------------------- number
    _operator("greater_than", "is greater than", "Numeric.", "Number", value_kind="number"),
    _operator("less_than", "is less than", "Numeric.", "Number", value_kind="number"),
    _operator(
        "greater_or_equal", "is at least", "Numeric.", "Number", value_kind="number"
    ),
    _operator("less_or_equal", "is at most", "Numeric.", "Number", value_kind="number"),
    # ------------------------------------------------------------ dates
    _operator(
        "older_than_days",
        "is more than N days ago",
        "The field's date is further in the past than N days.",
        "Dates",
        value_kind="number",
    ),
    _operator(
        "within_last_days",
        "is within the last N days",
        "The field's date is in the past, no more than N days back.",
        "Dates",
        value_kind="number",
    ),
    _operator(
        "due_within_days",
        "is due within N days",
        "The field's date is in the future, no more than N days ahead.",
        "Dates",
        value_kind="number",
    ),
    _operator("is_past", "is in the past", "", "Dates", takes_value=False),
    _operator("is_future", "is in the future", "", "Dates", takes_value=False),
    # ------------------------------------------------------------ change
    _operator(
        "changed",
        "changed",
        "This update altered the field. False on a create, which has no "
        "previous value to differ from.",
        "Changes",
        takes_value=False,
    ),
    _operator(
        "changed_to",
        "changed to",
        "This update set the field to exactly this value.",
        "Changes",
    ),
    _operator(
        "changed_from",
        "changed from",
        "The field held exactly this value before the update.",
        "Changes",
    ),
    # --------------------------------------------------------- ownership
    _operator(
        "owned_by_actor",
        "is owned by whoever triggered this",
        "The record's owner is the person whose action started the workflow.",
        "Ownership",
        takes_value=False,
    ),
    _operator(
        "is_unassigned",
        "has no owner",
        "",
        "Ownership",
        takes_value=False,
    ),
    # -------------------------------------------------------------- tags
    _operator("has_tag", "has tag", "The tag list contains this value.", "Tags"),
    _operator("not_has_tag", "does not have tag", "", "Tags"),
)

#: How a group combines its comparisons.
GROUP_MODES = ("all", "any")

CONDITION_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        key="mode",
        label="Match",
        kind="select",
        options=(
            FieldOption("all", "All of these"),
            FieldOption("any", "Any of these"),
        ),
        default="all",
    ),
)


# --------------------------------------------------------------- evaluation


def _as_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float | Decimal):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except (InvalidOperation, ValueError):
            return None
    return None


def _as_datetime(value: Any) -> datetime | None:
    """Parse a payload value into an aware datetime, or None.

    Payloads are JSON, so dates arrive as ISO strings. A naive one is assumed
    UTC — every timestamp this system stores is `timestamptz`, so a naive
    value means the serialiser dropped the offset rather than that the moment
    is genuinely local.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list | tuple):
        return [_text(item).strip().lower() for item in value]
    return [part.strip().lower() for part in _text(value).split(",") if part.strip()]


@dataclass(frozen=True, slots=True)
class Comparison:
    field: str
    operator: str
    value: Any = None


def evaluate_comparison(
    comparison: Comparison,
    *,
    record: dict[str, Any],
    previous: dict[str, Any],
    actor_id: str | None,
    now: datetime | None = None,
) -> bool:
    """One comparison against the trigger payload.

    `record` is the entity snapshot, `previous` the before-image on an update
    (empty on a create). Both come from the event, not from a fresh read — a
    condition must see the state that triggered it, not whatever it has become
    by the time the run gets scheduled.
    """
    moment = now or datetime.now(UTC)
    operator = comparison.operator
    left = record.get(comparison.field)
    expected = comparison.value

    # --- presence -------------------------------------------------------
    if operator == "is_set":
        return left not in (None, "", [], {})
    if operator == "is_empty":
        return left in (None, "", [], {})

    # --- change detection ----------------------------------------------
    if operator in ("changed", "changed_to", "changed_from"):
        if not previous:
            # A create has no before-image. "Changed" is false rather than
            # true-by-default: a workflow that fires on both create and change
            # says so with two triggers.
            return False
        before = previous.get(comparison.field)
        if comparison.field not in previous:
            return False
        match operator:
            case "changed":
                return before != left
            case "changed_to":
                return before != left and _text(left) == _text(expected)
            case _:
                return before != left and _text(before) == _text(expected)

    # --- ownership -------------------------------------------------------
    if operator == "owned_by_actor":
        owner = record.get("owner_id") or record.get("assignee_id")
        return bool(owner and actor_id and _text(owner) == _text(actor_id))
    if operator == "is_unassigned":
        return not (record.get("owner_id") or record.get("assignee_id"))

    # --- tags ------------------------------------------------------------
    if operator in ("has_tag", "not_has_tag"):
        tags = _as_list(record.get("tags"))
        present = _text(expected).strip().lower() in tags
        return present if operator == "has_tag" else not present

    # A missing field never matches anything below this point. Not an error,
    # not "matches empty" — the branch is simply not taken, and the step output
    # records which field was absent.
    if left is None:
        return False

    # --- text ------------------------------------------------------------
    match operator:
        case "equals":
            return _text(left) == _text(expected)
        case "not_equals":
            return _text(left) != _text(expected)
        case "contains":
            return _text(expected).lower() in _text(left).lower()
        case "not_contains":
            return _text(expected).lower() not in _text(left).lower()
        case "starts_with":
            return _text(left).lower().startswith(_text(expected).lower())
        case "in_list":
            return _text(left).strip().lower() in _as_list(expected)

    # --- numbers ---------------------------------------------------------
    if operator in ("greater_than", "less_than", "greater_or_equal", "less_or_equal"):
        # Typed by the operator, not guessed from the values: `"10" > "9"` has
        # opposite answers as text and as a number, and picking by inspection
        # is a bug nobody finds.
        left_number, right_number = _as_decimal(left), _as_decimal(expected)
        if left_number is None or right_number is None:
            return False
        match operator:
            case "greater_than":
                return left_number > right_number
            case "less_than":
                return left_number < right_number
            case "greater_or_equal":
                return left_number >= right_number
            case _:
                return left_number <= right_number

    # --- dates -----------------------------------------------------------
    if operator in (
        "older_than_days",
        "within_last_days",
        "due_within_days",
        "is_past",
        "is_future",
    ):
        moment_value = _as_datetime(left)
        if moment_value is None:
            return False
        match operator:
            case "is_past":
                return moment_value < moment
            case "is_future":
                return moment_value > moment
        days = _as_decimal(expected)
        if days is None:
            return False
        window = timedelta(days=float(days))
        match operator:
            case "older_than_days":
                return moment_value < moment - window
            case "within_last_days":
                return moment - window <= moment_value <= moment
            case _:
                return moment <= moment_value <= moment + window

    logger.warning("unknown_condition_operator", extra={"operator": operator})
    return False


def evaluate_group(
    comparisons: list[Comparison],
    mode: str,
    *,
    record: dict[str, Any],
    previous: dict[str, Any],
    actor_id: str | None,
    now: datetime | None = None,
) -> bool:
    """Combine comparisons with ALL or ANY.

    An empty group is true. A condition node with nothing configured is a
    passthrough rather than a dead end — which is what a half-built draft
    should do, and publishing validates that it is not empty anyway.
    """
    if not comparisons:
        return True

    results = (
        evaluate_comparison(
            comparison,
            record=record,
            previous=previous,
            actor_id=actor_id,
            now=now,
        )
        for comparison in comparisons
    )
    return all(results) if mode == "all" else any(results)
