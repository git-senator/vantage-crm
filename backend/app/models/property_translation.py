"""PropertyTranslation — a listing's text in one language.

Why a table rather than a JSONB column on ``properties``: every property
mutation writes an audit entry in the same transaction (see
``PropertyService``). A background translator that wrote into the property row
would forge an audit entry per translation and move each listing's
last-modified, so forty-one imported listings would read as edited by a robot
and the audit log — which exists to answer *who changed this* — would start
lying. Writing here leaves the listing and its history untouched.

The source text itself stays on ``properties``. A listing is written in one
language (``properties.source_locale``) and translated into the others; there
is no row here for the source locale, because the source is not a translation
of anything.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

#: Every language the CRM renders. Mirrors `frontend/src/i18n/config.ts`; the
#: two lists are short, change together, and a mismatch surfaces immediately as
#: a check-constraint violation rather than as silently missing text.
LOCALES = ("en", "pt-BR", "ru")

#: The language assumed for a listing whose author never said. Matches the
#: frontend's DEFAULT_LOCALE.
DEFAULT_LOCALE = "en"


class PropertyTranslation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "property_translations"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # CASCADE, unlike most FKs here: a translation has no meaning without its
    # listing, and nothing else references it. Deleting the listing should not
    # leave orphans that only a sweep would ever find.
    property_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("properties.id", ondelete="CASCADE"),
        nullable=False,
    )

    locale: Mapped[str] = mapped_column(String(10), nullable=False)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    features: Mapped[list[str]] = mapped_column(
        postgresql.ARRAY(String(60)), nullable=False, default=list, server_default="{}"
    )

    # ---------------------------------------------------------- provenance
    #: False once a person has edited this text. The distinction drives two
    #: behaviours: the UI marks machine output as such, and regeneration
    #: refuses to overwrite what a human wrote.
    is_machine: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    #: Fingerprint of the source text this was translated from. When the source
    #: changes the fingerprint diverges, which is how regeneration knows there
    #: is work to do without diffing prose.
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    #: The source moved on and this translation is human-written, so it was not
    #: regenerated. Someone has to look at it — a machine does not throw away a
    #: person's work, and it does not silently keep serving text that no longer
    #: matches the listing either.
    is_stale: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    translated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    edited_by_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    edited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # One translation per language per listing. The unique constraint is
        # what lets the writer upsert instead of read-then-write, so two
        # concurrent jobs for the same listing cannot produce duplicates.
        UniqueConstraint("property_id", "locale", name="uq_property_translation"),
        CheckConstraint(
            "locale IN ('en', 'pt-BR', 'ru')",
            name="ck_property_translations_locale",
        ),
        # A human edit is a fact with an author and a time, or it did not
        # happen. Written as an equivalence so a half-recorded edit — text
        # attributed to nobody, or an author with no timestamp — cannot exist.
        CheckConstraint(
            "(edited_by_id IS NULL) = (edited_at IS NULL)",
            name="ck_property_translations_edit_provenance",
        ),
        # The sweep's query: everything stale or awaiting a first translation,
        # within one tenant.
        Index(
            "ix_property_translations_stale",
            "organization_id",
            "is_stale",
        ),
        Index("ix_property_translations_property", "property_id"),
    )
