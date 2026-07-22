"""Attachment — a file's metadata, without the file (yet).

Phase 2.8 ships the **placeholder architecture** for documents: the row that
records that a file belongs to a record, who attached it, and where it will
live once object storage exists — but no bytes, no bucket, no upload path. That
is Phase 3, and this table is shaped so Phase 3 is an additive change rather
than a migration.

The design decisions that make that true:

  * **`status` is a lifecycle, starting at `pending_upload`.** A registered
    attachment is metadata waiting for content. Phase 3's post-upload pipeline
    moves it to `available` (or `quarantined` if the virus scan trips). Nothing
    in the CRM serves a row that is not `available`, so shipping the column now
    means the UI's "processing…" state is already correct.
  * **`storage_key` is nullable and unset.** It is the object key the file
    *will* have; the service computes a deterministic candidate at registration
    so the eventual upload has a target, but no object exists behind it yet.
  * **`checksum_sha256` and `size_bytes` are nullable.** They are populated by
    the upload pipeline from the actual bytes — trusting a client-declared size
    or hash is exactly the mistake the Phase 3 verification step exists to
    prevent.

Polymorphic by (`entity_type`, `entity_id`) like notes and activities, and
gated through the same `EntityAccess` resolver — you cannot attach to, or see
attachments on, a record you cannot read.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.user import User

#: Lifecycle. Only `available` rows are ever served to the CRM.
ATTACHMENT_STATUSES = ("pending_upload", "available", "quarantined", "failed")

#: Records a file can hang off. Aligned with EntityAccess's vocabulary so the
#: same readability resolver governs attachments.
ATTACHMENT_ENTITY_TYPES = ("lead", "client", "property", "deal", "task")

#: The only backend today is a placeholder. S3 lands in Phase 3.
ATTACHMENT_STORAGE_BACKENDS = ("s3",)


class Attachment(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "attachments"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    uploaded_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=False
    )

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    #: The client-declared MIME type. Phase 3 re-derives it from magic bytes and
    #: refuses to serve a mismatch — this column is a hint, never trusted.
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)

    #: Populated by the upload pipeline from the real bytes; null until then.
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    storage_backend: Mapped[str] = mapped_column(
        String(20), nullable=False, default="s3", server_default="s3"
    )
    #: The object key the file *will* occupy. Set at registration; no object
    #: exists behind it until Phase 3.
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending_upload",
        server_default="pending_upload",
    )

    uploader: Mapped[User | None] = relationship(
        foreign_keys=[uploaded_by], lazy="joined"
    )

    __table_args__ = (
        CheckConstraint("length(filename) > 0", name="ck_attachments_filename"),
        CheckConstraint(
            "status IN ('pending_upload', 'available', 'quarantined', 'failed')",
            name="ck_attachments_status",
        ),
        CheckConstraint(
            "entity_type IN ('lead', 'client', 'property', 'deal', 'task')",
            name="ck_attachments_entity_type",
        ),
        CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name="ck_attachments_size",
        ),
        Index(
            "ix_attachments_entity",
            "organization_id",
            "entity_type",
            "entity_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_attachments_org_created_id",
            "organization_id",
            "created_at",
            "id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_attachments_org_status",
            "organization_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<Attachment {self.id} {self.status}>"
