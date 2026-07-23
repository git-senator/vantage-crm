"""Turning a validated report specification into SQL.

Two shapes come out of here and they are genuinely different queries, not one
query with a flag:

  * **detail** — rows, the selected columns, ordered and bounded;
  * **grouped** — one row per distinct combination of the group-by fields, with
    the requested aggregates.

Everything reaching this module has already been resolved against the registry
in `datasets.py`, so a column here is a `InstrumentedAttribute` the backend
chose — never a string that came from a request. `build_*` accepts the resolved
`ReportSpec` and nothing else; there is no overload that takes names.

**The row cap is not a paging convenience.** A report is materialised into a
file in memory before it is written to storage, so an unbounded report is an
unbounded allocation in a worker shared with every other tenant. `MAX_ROWS`
bounds it, and the run records that it was truncated rather than quietly
returning a prefix — a spreadsheet that is silently missing its tail is worse
than one that says it is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Select, and_, func, select
from sqlalchemy.sql.elements import ColumnElement

from app.core.exceptions import ConflictError
from app.reporting.datasets import (
    OPERATORS_BY_TYPE,
    DatasetDefinition,
    FieldDefinition,
    get_dataset,
)

#: The hard ceiling on rows a single report may materialise. Chosen so the
#: largest XLSX this produces stays in the tens of megabytes: openpyxl's
#: write-only workbook is still an in-memory structure, and a worker that OOMs
#: takes every other tenant's job with it.
MAX_ROWS = 50_000

#: The default when a caller does not ask for one. Small enough that an
#: accidental unfiltered report on the biggest table is not an incident.
DEFAULT_LIMIT = 1_000


@dataclass(frozen=True, slots=True)
class ResolvedFilter:
    field: FieldDefinition
    operator: str
    value: Any


@dataclass(frozen=True, slots=True)
class ResolvedAggregate:
    key: str
    label: str
    function: str
    #: `None` for `count`, which counts rows rather than a column.
    field: FieldDefinition | None


@dataclass(frozen=True, slots=True)
class ReportSpec:
    """A report, resolved. Every name has become a registry object."""

    dataset: DatasetDefinition
    columns: tuple[FieldDefinition, ...]
    filters: tuple[ResolvedFilter, ...]
    group_by: tuple[FieldDefinition, ...]
    aggregates: tuple[ResolvedAggregate, ...]
    sort_field: FieldDefinition | None
    sort_desc: bool
    limit: int
    include_sensitive: bool = False

    @property
    def is_grouped(self) -> bool:
        return bool(self.group_by)

    @property
    def headers(self) -> list[str]:
        if self.is_grouped:
            return [f.label for f in self.group_by] + [a.label for a in self.aggregates]
        return [f.label for f in self.columns]


# --------------------------------------------------------------- resolution


def resolve_spec(raw: dict[str, Any]) -> ReportSpec:
    """Validate a stored or submitted definition against the registry.

    Runs on every execution, not only on save. A definition saved last month is
    a document, and the registry it referenced may since have lost a field — so
    the check that a report is still valid belongs at the moment it runs, not
    only at the moment it was written.
    """
    try:
        dataset = get_dataset(str(raw.get("dataset", "")))
    except KeyError:
        raise ConflictError(
            f"Unknown dataset. Available: {', '.join(sorted(_dataset_keys()))}"
        ) from None

    include_sensitive = bool(raw.get("include_sensitive", False))

    columns = tuple(_field(dataset, key, include_sensitive) for key in raw.get("columns") or [])
    group_by = tuple(_group_field(dataset, key) for key in raw.get("group_by") or [])
    aggregates = tuple(_aggregate(dataset, spec) for spec in raw.get("aggregates") or [])
    filters = tuple(_filter(dataset, spec) for spec in raw.get("filters") or [])

    if group_by and not aggregates:
        # A grouped report with nothing to aggregate is a DISTINCT dressed up as
        # a report; the caller almost certainly meant to count.
        aggregates = (ResolvedAggregate(key="count", label="Count", function="count", field=None),)
    if not group_by and not columns:
        # An ungrouped report with no columns selected is empty. Default to
        # something useful rather than returning a file of blank rows.
        columns = tuple(f for f in dataset.fields.values() if not f.sensitive or include_sensitive)[
            :8
        ]

    sort_key = raw.get("sort") or (None if group_by else dataset.default_sort)
    sort_field = None
    if sort_key is not None:
        sort_field = _field(dataset, str(sort_key), include_sensitive=True)

    limit = int(raw.get("limit") or DEFAULT_LIMIT)
    if limit < 1:
        raise ConflictError("A report must return at least one row.")

    return ReportSpec(
        dataset=dataset,
        columns=columns,
        filters=filters,
        group_by=group_by,
        aggregates=aggregates,
        sort_field=sort_field,
        sort_desc=bool(raw.get("sort_desc", True)),
        limit=min(limit, MAX_ROWS),
        include_sensitive=include_sensitive,
    )


def _dataset_keys() -> list[str]:
    from app.reporting.datasets import DATASETS

    return list(DATASETS)


def _field(dataset: DatasetDefinition, key: str, include_sensitive: bool) -> FieldDefinition:
    try:
        definition = dataset.require_field(key)
    except KeyError:
        raise ConflictError(
            f"'{key}' is not a field on {dataset.label}. "
            f"Available: {', '.join(sorted(dataset.fields))}"
        ) from None
    if definition.sensitive and not include_sensitive:
        raise ConflictError(f"'{key}' is a sensitive field and must be requested explicitly.")
    return definition


def _group_field(dataset: DatasetDefinition, key: str) -> FieldDefinition:
    definition = _field(dataset, key, include_sensitive=True)
    if not definition.groupable:
        # High-cardinality grouping produces one group per row: a slow way to
        # render the list the caller could have asked for directly.
        raise ConflictError(f"'{key}' cannot be grouped — it is unique per record.")
    return definition


def _aggregate(dataset: DatasetDefinition, spec: Any) -> ResolvedAggregate:
    if not isinstance(spec, dict):
        raise ConflictError("Each aggregate must be an object.")

    function = str(spec.get("function", "count"))
    if function not in ("count", "sum", "avg", "min", "max"):
        raise ConflictError(f"Unknown aggregate function: {function}")

    if function == "count":
        return ResolvedAggregate(
            key=str(spec.get("key") or "count"),
            label=str(spec.get("label") or "Count"),
            function="count",
            field=None,
        )

    key = spec.get("field")
    if not key:
        raise ConflictError(f"{function} needs a field.")
    definition = _field(dataset, str(key), include_sensitive=True)
    if function in ("sum", "avg") and not definition.aggregatable:
        raise ConflictError(f"'{key}' is not numeric — {function} does not apply.")

    return ResolvedAggregate(
        key=str(spec.get("key") or f"{function}_{definition.key}"),
        label=str(spec.get("label") or f"{function.title()} of {definition.label}"),
        function=function,
        field=definition,
    )


def _filter(dataset: DatasetDefinition, spec: Any) -> ResolvedFilter:
    if not isinstance(spec, dict):
        raise ConflictError("Each filter must be an object.")

    definition = _field(dataset, str(spec.get("field", "")), include_sensitive=True)
    operator = str(spec.get("operator", "eq"))

    allowed = OPERATORS_BY_TYPE[definition.type]
    if operator not in allowed:
        raise ConflictError(
            f"'{operator}' does not apply to {definition.label}. Allowed: {', '.join(allowed)}"
        )

    value = _coerce(definition, operator, spec.get("value"))
    if definition.choices and operator in ("eq", "ne", "in", "not_in"):
        candidates = value if isinstance(value, list) else [value]
        unknown = [c for c in candidates if c not in definition.choices]
        if unknown:
            raise ConflictError(
                f"{definition.label} has no value {unknown[0]!r}. "
                f"Allowed: {', '.join(definition.choices)}"
            )

    return ResolvedFilter(field=definition, operator=operator, value=value)


def _coerce(definition: FieldDefinition, operator: str, value: Any) -> Any:
    """Parse a JSON value into the column's Python type.

    Coerced here rather than left to the driver: a money filter arriving as the
    string "500000" must become a Decimal, not a string Postgres casts with its
    own rules. `between` and `in` carry lists, and each element goes through the
    same conversion — a list is not an escape hatch from validation.
    """
    if operator in ("is_null", "is_not_null"):
        return None

    if operator in ("in", "not_in", "between"):
        if not isinstance(value, list) or not value:
            raise ConflictError(f"'{operator}' needs a list of values.")
        if operator == "between" and len(value) != 2:
            raise ConflictError("'between' needs exactly two values.")
        return [_coerce_scalar(definition, item) for item in value]

    return _coerce_scalar(definition, value)


def _coerce_scalar(definition: FieldDefinition, value: Any) -> Any:
    if value is None:
        return None
    try:
        match definition.type:
            case "integer":
                return int(value)
            case "number" | "money":
                return Decimal(str(value))
            case "boolean":
                return value if isinstance(value, bool) else str(value).lower() == "true"
            case "date":
                return value if isinstance(value, date) else date.fromisoformat(str(value))
            case "datetime":
                if isinstance(value, datetime):
                    return value
                return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            case _:
                return str(value)
    except (ValueError, TypeError, InvalidOperation):
        raise ConflictError(
            f"{value!r} is not a valid {definition.type} for {definition.label}."
        ) from None


# ------------------------------------------------------------ construction


def _predicate(resolved: ResolvedFilter) -> ColumnElement[bool]:
    """One filter, as SQL. The operator set is closed; there is no else-clause
    that falls through to raw text.

    The comparison branches are cast because an `InstrumentedAttribute[Any]`
    types its operators as returning `Any`. Casting states what SQLAlchemy
    actually produces, so a caller of this function keeps a real type instead of
    inheriting the `Any` — which in a module whose entire job is validation is
    worth the six annotations.
    """
    column = resolved.field.column
    value = resolved.value

    match resolved.operator:
        case "eq":
            return cast(ColumnElement[bool], column == value)
        case "ne":
            return cast(ColumnElement[bool], column != value)
        case "gt":
            return cast(ColumnElement[bool], column > value)
        case "gte":
            return cast(ColumnElement[bool], column >= value)
        case "lt":
            return cast(ColumnElement[bool], column < value)
        case "lte":
            return cast(ColumnElement[bool], column <= value)
        case "contains":
            # `ilike` with the value escaped: a user searching for "50%" must
            # not get a wildcard.
            return column.ilike(f"%{_escape_like(str(value))}%", escape="\\")
        case "starts_with":
            return column.ilike(f"{_escape_like(str(value))}%", escape="\\")
        case "in":
            return column.in_(value)
        case "not_in":
            return column.not_in(value)
        case "is_null":
            return column.is_(None)
        case "is_not_null":
            return column.is_not(None)
        case "between":
            return and_(column >= value[0], column <= value[1])
        case _:  # pragma: no cover — resolve_spec has already rejected this
            raise ConflictError(f"Unsupported operator: {resolved.operator}")


def _escape_like(value: str) -> str:
    """Neutralise LIKE metacharacters in user input.

    Without this, a filter for a client called "100%" matches everything — not a
    security hole, but a wrong answer that looks like a working feature.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _base(spec: ReportSpec, organization_id: UUID, owner_ids: list[UUID] | None) -> Any:
    """The WHERE clauses every report shares: tenant, soft delete, scope, filters.

    Tenant is applied here as well as by RLS. Belt and braces on purpose — RLS
    is the guarantee, but a query that also says what it means is one that
    cannot be silently widened by a session that forgot to bind the GUC.
    """
    dataset = spec.dataset
    clauses: list[ColumnElement[bool]] = [dataset.model.organization_id == organization_id]
    if dataset.soft_deleted:
        clauses.append(dataset.model.deleted_at.is_(None))
    if owner_ids is not None and dataset.owner_column is not None:
        clauses.append(dataset.owner_column.in_(owner_ids))
    clauses.extend(_predicate(f) for f in spec.filters)
    return clauses


def build_query(
    spec: ReportSpec, organization_id: UUID, owner_ids: list[UUID] | None
) -> Select[Any]:
    """The report, as one statement."""
    clauses = _base(spec, organization_id, owner_ids)

    if spec.is_grouped:
        columns: list[Any] = [f.column for f in spec.group_by]
        columns.extend(_aggregate_column(a) for a in spec.aggregates)
        query = select(*columns).select_from(spec.dataset.model).where(and_(*clauses))
        query = query.group_by(*[f.column for f in spec.group_by])
        # Ordered by the first aggregate descending: a grouped report is read
        # top-down, and the largest group is what the reader came for.
        first = _aggregate_column(spec.aggregates[0])
        return query.order_by(first.desc()).limit(spec.limit)

    query = (
        select(*[f.column for f in spec.columns])
        .select_from(spec.dataset.model)
        .where(and_(*clauses))
    )
    if spec.sort_field is not None:
        order = spec.sort_field.column
        query = query.order_by(order.desc() if spec.sort_desc else order.asc())
    return query.limit(spec.limit)


def build_count_query(
    spec: ReportSpec, organization_id: UUID, owner_ids: list[UUID] | None
) -> Select[Any]:
    """How many rows the report *would* return, ignoring the limit.

    Run alongside the report so a truncated run can say so. Costs one extra
    aggregate scan and buys the difference between "here are your 50,000 rows"
    and "here are 50,000 of your 214,000 rows".
    """
    clauses = _base(spec, organization_id, owner_ids)
    if spec.is_grouped:
        inner = (
            select(*[f.column for f in spec.group_by])
            .select_from(spec.dataset.model)
            .where(and_(*clauses))
            .group_by(*[f.column for f in spec.group_by])
            .subquery()
        )
        return select(func.count()).select_from(inner)
    return select(func.count()).select_from(spec.dataset.model).where(and_(*clauses))


def _aggregate_column(aggregate: ResolvedAggregate) -> Any:
    if aggregate.function == "count" or aggregate.field is None:
        return func.count().label(aggregate.key)
    column = aggregate.field.column
    return {
        "sum": func.sum(column),
        "avg": func.avg(column),
        "min": func.min(column),
        "max": func.max(column),
    }[aggregate.function].label(aggregate.key)


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_ROWS",
    "ReportSpec",
    "ResolvedAggregate",
    "ResolvedFilter",
    "build_count_query",
    "build_query",
    "resolve_spec",
]
