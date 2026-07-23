"""The dataset registry: what a report is allowed to ask about.

**There is no user SQL anywhere in this module, and there must never be.** A
report is a *specification* — a dataset key, a list of field keys, a list of
`(field, operator, value)` filters — and every one of those names is resolved
here against a fixed registry before a query is built. A name that is not in the
registry does not become a slow query or an error message with a table name in
it; it is rejected at validation with the list of names that are valid.

That is not defensive style, it is the entire security model of the feature. The
alternative designs all end in the same place: a filter that accepts a column
name becomes a filter that accepts `id) OR (1=1`, and a report builder that
accepts an expression is a SQL console with a nicer font. Postgres RLS would
still hold the tenant line, but scope, soft deletes and column-level exposure
would not.

**Each dataset declares the permission that gates it**, and reporting reuses it
rather than inventing one — the same rule analytics follows (docs/ANALYTICS.md
§2). A report over deals shows exactly the deals the caller could list, because
it resolves the caller's grant through the same scope resolver. `reports.view`
gates the reporting surface; the dataset's own permission gates the rows.

**`sensitive` fields** are declared but excluded from exports by default. A
report is a file that leaves the building — it gets emailed, printed, and left
in a downloads folder — so a column being visible in the UI is not on its own an
argument for putting it in a spreadsheet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.orm import InstrumentedAttribute

from app.models.activity import Activity
from app.models.client import Client
from app.models.deal import Deal
from app.models.lead import Lead
from app.models.property import Property
from app.models.task import Task

#: How a value is rendered and which operators apply to it.
FieldType = Literal["string", "number", "money", "integer", "date", "datetime", "boolean", "enum"]

#: What an aggregate column does. `count` needs no field.
Aggregate = Literal["count", "sum", "avg", "min", "max"]

#: Operators, deliberately closed. Each maps to a SQLAlchemy construction in
#: `query.py`; there is no pass-through.
Operator = Literal[
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "contains",
    "starts_with",
    "in",
    "not_in",
    "is_null",
    "is_not_null",
    "between",
]

#: Which operators each field type accepts. Enforced, so a `contains` on a
#: numeric column is a validation error rather than a cast Postgres performs
#: and then cannot index.
OPERATORS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "string": ("eq", "ne", "contains", "starts_with", "in", "not_in", "is_null", "is_not_null"),
    "enum": ("eq", "ne", "in", "not_in", "is_null", "is_not_null"),
    "number": ("eq", "ne", "gt", "gte", "lt", "lte", "between", "is_null", "is_not_null"),
    "money": ("eq", "ne", "gt", "gte", "lt", "lte", "between", "is_null", "is_not_null"),
    "integer": (
        "eq",
        "ne",
        "gt",
        "gte",
        "lt",
        "lte",
        "between",
        "in",
        "not_in",
        "is_null",
        "is_not_null",
    ),
    "date": ("eq", "ne", "gt", "gte", "lt", "lte", "between", "is_null", "is_not_null"),
    "datetime": ("eq", "ne", "gt", "gte", "lt", "lte", "between", "is_null", "is_not_null"),
    "boolean": ("eq", "ne"),
}


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    key: str
    label: str
    type: FieldType
    column: InstrumentedAttribute[Any]
    #: Whether rows may be grouped by this field. False for anything
    #: high-cardinality enough that grouping produces one row per record — a
    #: "grouped" report with 40,000 groups is a slow way to render a list.
    groupable: bool = True
    #: Whether SUM/AVG mean anything here. Only numeric fields.
    aggregatable: bool = False
    #: Excluded from exports unless explicitly requested. See the module
    #: docstring.
    sensitive: bool = False
    #: Allowed values, for enums. Filters are validated against it, so a typo in
    #: a saved report surfaces as an error rather than silently zero rows.
    choices: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DatasetDefinition:
    key: str
    label: str
    description: str
    model: type[Any]
    #: The grant that decides which rows the caller sees. Reused from the CRM,
    #: never a reporting-specific permission.
    permission: str
    #: The column a scope predicate applies to. Tasks anchor on `assignee_id`
    #: because work belongs to whoever must do it — the same asymmetry the task
    #: list endpoint has.
    owner_column: InstrumentedAttribute[Any] | None
    #: The default ordering column, newest-first.
    default_sort: str
    fields: dict[str, FieldDefinition] = field(default_factory=dict)
    #: Whether the model carries `deleted_at`. Activities do not: a timeline
    #: entry records something that happened.
    soft_deleted: bool = True

    def require_field(self, key: str) -> FieldDefinition:
        found = self.fields.get(key)
        if found is None:
            raise KeyError(key)
        return found


def _fields(*definitions: FieldDefinition) -> dict[str, FieldDefinition]:
    return {definition.key: definition for definition in definitions}


LEADS = DatasetDefinition(
    key="leads",
    label="Leads",
    description="Prospects, their source, stage and value.",
    model=Lead,
    permission="leads.view",
    owner_column=Lead.owner_id,
    default_sort="created_at",
    soft_deleted=True,
    fields=_fields(
        FieldDefinition("first_name", "First name", "string", Lead.first_name, groupable=False),
        FieldDefinition("last_name", "Last name", "string", Lead.last_name, groupable=False),
        FieldDefinition("email", "Email", "string", Lead.email, groupable=False, sensitive=True),
        FieldDefinition("phone", "Phone", "string", Lead.phone, groupable=False, sensitive=True),
        FieldDefinition("stage", "Stage", "enum", Lead.stage),
        FieldDefinition("status", "Status", "enum", Lead.status),
        FieldDefinition("source", "Source", "enum", Lead.source),
        FieldDefinition("temperature", "Temperature", "enum", Lead.temperature),
        FieldDefinition(
            "budget_min",
            "Budget (min)",
            "money",
            Lead.budget_min,
            groupable=False,
            aggregatable=True,
        ),
        FieldDefinition(
            "budget_max",
            "Budget (max)",
            "money",
            Lead.budget_max,
            groupable=False,
            aggregatable=True,
        ),
        FieldDefinition(
            "score", "Score", "integer", Lead.score, groupable=False, aggregatable=True
        ),
        FieldDefinition(
            "preferred_location", "Preferred location", "string", Lead.preferred_location
        ),
        FieldDefinition("owner_id", "Owner", "string", Lead.owner_id),
        FieldDefinition("created_at", "Created", "datetime", Lead.created_at, groupable=False),
        FieldDefinition(
            "converted_at", "Converted", "datetime", Lead.converted_at, groupable=False
        ),
        FieldDefinition(
            "last_contacted_at",
            "Last contacted",
            "datetime",
            Lead.last_contacted_at,
            groupable=False,
        ),
    ),
)

CLIENTS = DatasetDefinition(
    key="clients",
    label="Clients",
    description="Converted and directly-created clients.",
    model=Client,
    permission="contacts.view",
    owner_column=Client.owner_id,
    default_sort="created_at",
    fields=_fields(
        FieldDefinition("first_name", "First name", "string", Client.first_name, groupable=False),
        FieldDefinition("last_name", "Last name", "string", Client.last_name, groupable=False),
        FieldDefinition("company_name", "Company", "string", Client.company_name),
        FieldDefinition("email", "Email", "string", Client.email, groupable=False, sensitive=True),
        FieldDefinition("phone", "Phone", "string", Client.phone, groupable=False, sensitive=True),
        FieldDefinition("type", "Type", "enum", Client.type),
        FieldDefinition("status", "Status", "enum", Client.status),
        FieldDefinition(
            "lifetime_value",
            "Lifetime value",
            "money",
            Client.lifetime_value,
            groupable=False,
            aggregatable=True,
        ),
        FieldDefinition(
            "client_since", "Client since", "date", Client.client_since, groupable=False
        ),
        FieldDefinition("owner_id", "Owner", "string", Client.owner_id),
        FieldDefinition("created_at", "Created", "datetime", Client.created_at, groupable=False),
    ),
)

PROPERTIES = DatasetDefinition(
    key="properties",
    label="Properties",
    description="Listings, their status, price and characteristics.",
    model=Property,
    permission="properties.view",
    # Listings anchor on the listing agent, not a generic owner — the same
    # column the property list endpoint scopes on.
    owner_column=Property.listing_agent_id,
    default_sort="created_at",
    fields=_fields(
        FieldDefinition("title", "Title", "string", Property.title, groupable=False),
        FieldDefinition("mls_number", "MLS", "string", Property.mls_number, groupable=False),
        FieldDefinition("status", "Status", "enum", Property.status),
        FieldDefinition("property_type", "Type", "enum", Property.property_type),
        FieldDefinition("city", "City", "string", Property.city),
        FieldDefinition("state", "State", "string", Property.state),
        FieldDefinition("postal_code", "Postal code", "string", Property.postal_code),
        FieldDefinition(
            "price", "Price", "money", Property.price, groupable=False, aggregatable=True
        ),
        FieldDefinition("bedrooms", "Bedrooms", "integer", Property.bedrooms, aggregatable=True),
        FieldDefinition("bathrooms", "Bathrooms", "number", Property.bathrooms, aggregatable=True),
        FieldDefinition(
            "square_feet",
            "Square feet",
            "integer",
            Property.square_feet,
            groupable=False,
            aggregatable=True,
        ),
        FieldDefinition(
            "year_built", "Year built", "integer", Property.year_built, aggregatable=True
        ),
        FieldDefinition("listed_at", "Listed", "date", Property.listed_at, groupable=False),
        FieldDefinition(
            "view_count",
            "Views",
            "integer",
            Property.view_count,
            groupable=False,
            aggregatable=True,
        ),
        FieldDefinition("listing_agent_id", "Listing agent", "string", Property.listing_agent_id),
        FieldDefinition("created_at", "Created", "datetime", Property.created_at, groupable=False),
    ),
)

DEALS = DatasetDefinition(
    key="deals",
    label="Deals",
    description="Pipeline, value, commission and outcomes.",
    model=Deal,
    permission="deals.view",
    owner_column=Deal.owner_id,
    default_sort="created_at",
    fields=_fields(
        FieldDefinition("title", "Title", "string", Deal.title, groupable=False),
        FieldDefinition("value", "Value", "money", Deal.value, groupable=False, aggregatable=True),
        FieldDefinition("currency", "Currency", "enum", Deal.currency),
        FieldDefinition(
            "commission_amount",
            "Commission",
            "money",
            Deal.commission_amount,
            groupable=False,
            aggregatable=True,
        ),
        FieldDefinition(
            "commission_rate",
            "Commission rate",
            "number",
            Deal.commission_rate,
            groupable=False,
            aggregatable=True,
        ),
        FieldDefinition(
            "probability", "Probability", "integer", Deal.probability, aggregatable=True
        ),
        FieldDefinition("priority", "Priority", "enum", Deal.priority),
        FieldDefinition("stage_id", "Stage", "string", Deal.stage_id),
        FieldDefinition("pipeline_id", "Pipeline", "string", Deal.pipeline_id),
        FieldDefinition(
            "expected_close_date",
            "Expected close",
            "date",
            Deal.expected_close_date,
            groupable=False,
        ),
        FieldDefinition(
            "actual_close_date", "Actual close", "date", Deal.actual_close_date, groupable=False
        ),
        FieldDefinition("lost_reason", "Lost reason", "string", Deal.lost_reason),
        FieldDefinition("owner_id", "Owner", "string", Deal.owner_id),
        FieldDefinition("client_id", "Client", "string", Deal.client_id, groupable=False),
        FieldDefinition("created_at", "Created", "datetime", Deal.created_at, groupable=False),
    ),
)

TASKS = DatasetDefinition(
    key="tasks",
    label="Tasks",
    description="Work items, their status and who owes them.",
    model=Task,
    permission="tasks.view",
    # Tasks anchor scope on the assignee: work belongs to whoever must do it,
    # not whoever asked for it.
    owner_column=Task.assignee_id,
    default_sort="created_at",
    fields=_fields(
        FieldDefinition("title", "Title", "string", Task.title, groupable=False),
        FieldDefinition("status", "Status", "enum", Task.status),
        FieldDefinition("priority", "Priority", "enum", Task.priority),
        FieldDefinition("assignee_id", "Assignee", "string", Task.assignee_id),
        FieldDefinition("entity_type", "Linked to", "enum", Task.entity_type),
        FieldDefinition("due_at", "Due", "datetime", Task.due_at, groupable=False),
        FieldDefinition(
            "completed_at", "Completed", "datetime", Task.completed_at, groupable=False
        ),
        FieldDefinition("created_at", "Created", "datetime", Task.created_at, groupable=False),
    ),
)

ACTIVITIES = DatasetDefinition(
    key="activities",
    label="Activities",
    description="Logged calls, emails, meetings and showings.",
    model=Activity,
    permission="activities.view",
    owner_column=Activity.actor_id,
    default_sort="occurred_at",
    # No soft delete: a timeline entry records something that happened, and
    # history that can be removed is not history.
    soft_deleted=False,
    fields=_fields(
        FieldDefinition("type", "Type", "enum", Activity.type),
        FieldDefinition("subject", "Subject", "string", Activity.subject, groupable=False),
        FieldDefinition("entity_type", "Entity", "enum", Activity.entity_type),
        FieldDefinition("actor_id", "Actor", "string", Activity.actor_id),
        FieldDefinition(
            "occurred_at", "Occurred", "datetime", Activity.occurred_at, groupable=False
        ),
    ),
)

DATASETS: dict[str, DatasetDefinition] = {
    dataset.key: dataset for dataset in (LEADS, CLIENTS, PROPERTIES, DEALS, TASKS, ACTIVITIES)
}


def get_dataset(key: str) -> DatasetDefinition:
    dataset = DATASETS.get(key)
    if dataset is None:
        raise KeyError(key)
    return dataset


def validate_registry() -> None:
    """Fail at import on a registry that would produce a broken report.

    Cheap to check here, expensive to discover when a scheduled report has been
    silently emitting an empty column for a month.
    """
    for dataset in DATASETS.values():
        if dataset.default_sort not in dataset.fields:
            raise ValueError(f"{dataset.key}: default_sort {dataset.default_sort} is not a field")
        for key, definition in dataset.fields.items():
            if key != definition.key:
                raise ValueError(
                    f"{dataset.key}: field registered under {key} but named {definition.key}"
                )
            if definition.type not in OPERATORS_BY_TYPE:
                raise ValueError(f"{dataset.key}.{key}: unknown type {definition.type}")
            if definition.aggregatable and definition.type not in (
                "number",
                "money",
                "integer",
            ):
                raise ValueError(f"{dataset.key}.{key}: only numeric fields aggregate")


validate_registry()


__all__ = [
    "ACTIVITIES",
    "CLIENTS",
    "DATASETS",
    "DEALS",
    "LEADS",
    "OPERATORS_BY_TYPE",
    "PROPERTIES",
    "TASKS",
    "Aggregate",
    "DatasetDefinition",
    "FieldDefinition",
    "FieldType",
    "Operator",
    "get_dataset",
]
