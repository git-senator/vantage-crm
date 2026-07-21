"""Client contracts.

Same Create / Update / Read split as leads, and for the same reasons: Create
requires what a row needs, Update makes everything optional, Read declares
exactly what crosses the boundary so a new column cannot silently start being
exposed. Neither Create nor Update accepts `organization_id`, `source_lead_id`
or audit columns.

Two deliberate differences from `LeadUpdate`:

  * `status` **is** editable. A client going dormant is a field edit with no
    side effects. A lead converting is not, which is why LeadUpdate omits it.
  * `source_lead_id` is read-only. It is written by conversion and nowhere else;
    accepting it from a client would let anyone forge a funnel link.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

ClientType = Literal["buyer", "seller", "investor", "landlord", "tenant", "other"]
ClientStatus = Literal["active", "under_contract", "dormant", "past"]

# Bounded so a client cannot post a megabyte of tags. The database has matching
# CHECK constraints — validation here gives a good error, the constraint
# guarantees the invariant.
Name = Annotated[str, Field(min_length=1, max_length=100)]
CompanyName = Annotated[str, Field(min_length=1, max_length=200)]
Money = Annotated[Decimal, Field(ge=0, le=Decimal("99999999999.99"), decimal_places=2)]


class ClientOwner(BaseModel):
    """Minimal owner projection for list and detail views."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    full_name: str
    initials: str
    avatar_hue: int


def has_identity(
    first_name: str | None, last_name: str | None, company_name: str | None
) -> bool:
    """Mirrors ck_clients_identity: a full person name, or a company name.

    Public because `ClientService.update_client` re-checks it against the
    merged record — a PATCH can only be validated against the result, not
    against the payload alone.
    """
    return (first_name is not None and last_name is not None) or (
        company_name is not None
    )


class ClientBase(BaseModel):
    first_name: Name | None = None
    last_name: Name | None = None
    company_name: CompanyName | None = None
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    type: ClientType = "buyer"
    status: ClientStatus = "active"
    address: dict[str, Any] = Field(default_factory=dict)
    lifetime_value: Money | None = None
    currency: str = Field(default="USD", min_length=3, max_length=3)
    client_since: date | None = None
    notes: str | None = Field(default=None, max_length=10_000)
    tags: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _identity_is_present(self) -> ClientBase:
        """Mirrors the database CHECK.

        Validating here as well turns a 500 from a constraint violation into a
        422 the form can actually render against a field.
        """
        if not has_identity(self.first_name, self.last_name, self.company_name):
            raise ValueError(
                "Provide either a first and last name, or a company name."
            )
        return self


class ClientCreate(ClientBase):
    """New client. Owner defaults to the creator when omitted."""

    owner_id: UUID | None = None


class ClientUpdate(BaseModel):
    """Partial update. Every field optional; unset fields are untouched.

    The identity rule cannot be fully checked here — a PATCH carrying only
    `company_name=None` is valid in isolation but may empty the record's last
    identity. The service re-checks against the merged result, and the database
    CHECK is the backstop.
    """

    first_name: Name | None = None
    last_name: Name | None = None
    company_name: CompanyName | None = None
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    type: ClientType | None = None
    status: ClientStatus | None = None
    address: dict[str, Any] | None = None
    lifetime_value: Money | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    client_since: date | None = None
    notes: str | None = Field(default=None, max_length=10_000)
    tags: list[str] | None = Field(default=None, max_length=20)
    owner_id: UUID | None = None


class ClientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    first_name: str | None
    last_name: str | None
    company_name: str | None
    display_name: str
    is_company: bool
    email: str | None
    phone: str | None
    type: str
    status: str
    address: dict[str, Any]
    lifetime_value: Decimal | None
    currency: str
    client_since: date | None
    notes: str | None
    tags: list[str]
    custom_fields: dict[str, Any]
    source_lead_id: UUID | None
    owner: ClientOwner | None
    created_at: datetime
    updated_at: datetime


class ClientFilters(BaseModel):
    """Explicit filter parameters.

    Deliberately not a generic query DSL, for the reasons given in
    `LeadFilters` — injection surface, unbounded queries, and index
    requirements nobody can reason about.
    """

    search: str | None = Field(default=None, max_length=200)
    type: ClientType | None = None
    status: ClientStatus | None = None
    owner_id: UUID | None = None
    tag: str | None = Field(default=None, max_length=40)


class ClientAssign(BaseModel):
    owner_id: UUID


class ClientConvert(BaseModel):
    """What conversion needs beyond what the lead already carries.

    Identity, contact details, tags, notes and owner are inherited from the
    lead, so this is only the client-specific classification. `company_name`
    is here because a lead is always a person by schema while the client it
    becomes may be the entity that person represents.
    """

    type: ClientType = "buyer"
    company_name: CompanyName | None = None
    client_since: date | None = None
