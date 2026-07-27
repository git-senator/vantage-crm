"""API key — a machine credential for a tenant.

Phase 7.1. The counterpart of a user session for programmatic access: a
long-lived, revocable credential that authenticates a *machine* rather than a
person, and carries its own subset of RBAC scopes.

Two things mirror `refresh_tokens` exactly, and for the same reasons:

  * **Only a SHA-256 hash is stored** (`token_hash`, unique). The raw secret is
    shown once at creation and never again — a database disclosure yields no
    usable keys.
  * **Tenant is resolved before RLS permits a read.** Authentication is handed
    an opaque key with no tenant context; a SECURITY DEFINER lookup returns the
    organization id, the tenant is bound, and the row is then read under RLS.

What differs: a key is anchored to the user who created it (`created_by`). That
anchor is deliberate — it is the audit actor, it lets `own`/`team` scopes
resolve, and it is the ceiling on the key's authority. A key can never out-grant
the person who minted it, and if that person loses a permission the key loses it
too (enforced at authentication time in `ApiKeyService`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin

#: The two environments a key belongs to. A `live` key acts on real tenant data;
#: a `sandbox` key is a developer's test credential (Phase 7.6) — it authenticates
#: the same way but is exempt from the plan's API-access gate and API-key quota,
#: so a developer can try the API on any plan without spending a paid slot.
KEY_ENVIRONMENTS = ("live", "sandbox")


class ApiKey(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "api_keys"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # The user who created the key: audit actor, scope anchor, and authority
    # ceiling. SET NULL rather than CASCADE — deleting a user must not silently
    # drop the key's audit trail — but a key whose creator is gone authenticates
    # as no one and is refused (see ApiKeyService.authenticate).
    created_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: A human label so an operator can tell keys apart ("CI deploy", "Zapier").
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    #: `live` or `sandbox` (Phase 7.6). Sandbox keys are test credentials that
    #: skip the plan gate and quota; they authenticate identically otherwise.
    environment: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="live"
    )

    # SHA-256 of the raw key. The raw value exists only in the creation response.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    #: Public identifier shown in listings (prefix + first 8 of the secret).
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Last four characters, to disambiguate keys in a list.
    last_four: Mapped[str] = mapped_column(String(8), nullable=False)

    #: The key's granted scopes: `{permission_key: scope}`. Bounded at creation
    #: to a subset of the creator's own grants, and re-bounded at authentication.
    scopes: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="{}"
    )

    #: Optional expiry. NULL is a non-expiring key.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Set (throttled) on use, so an operator can spot dormant or leaked keys.
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_api_keys_org", "organization_id"),
        CheckConstraint(
            "environment IN ('live', 'sandbox')",
            name="ck_api_keys_environment",
        ),
    )

    @property
    def is_active(self) -> bool:
        """Usable right now: not revoked, not past expiry."""
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > datetime.now(UTC)

    @property
    def is_sandbox(self) -> bool:
        """A test credential, not billed and not gated on the plan's API access."""
        return self.environment == "sandbox"

    def __repr__(self) -> str:
        # Never include token_hash.
        return f"<ApiKey {self.id} {self.prefix}...{self.last_four}>"
