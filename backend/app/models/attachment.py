"""Attachment — a file's metadata, and (since Phase 3.1) its bytes.

Phase 2.8 shipped this table as a placeholder: a row recording that a file
belongs to a record, the `storage_key` it *would* occupy, and a `status`
lifecycle stuck at `pending_upload`. Phase 3.1 filled the seam in — the shape
was designed for exactly this and the migration is purely additive.

The lifecycle, which is the load-bearing part:

    pending_upload ──(client PUTs, then finalize verifies)──> available
          │                                                       │
          │ upload window elapses                                 │ scan trips
          ▼                                                       ▼
       failed  ◀──(bytes contradict the declared type)──      quarantined

  * **Only `available` rows are ever served.** A download URL is minted for no
    other status, so a file that failed verification or tripped the scanner
    cannot be handed out by any code path that forgets to check.
  * **`size_bytes` and `checksum_sha256` come from storage, never the client.**
    They are populated at finalization by reading the object back. A client that
    could declare its own size or hash would be trusted to describe a file it
    never sent — which is the whole attack.
  * **`content_type` is re-derived from magic bytes.** The column holds the
    verified type after finalization; before that it is the client's claim.
  * **`upload_expires_at`** bounds how long a registered row may sit unclaimed.
    Past it the presigned PUT is dead and the sweeper reaps the row, so an
    abandoned registration cannot linger as a writable slot in the bucket.

Polymorphic by (`entity_type`, `entity_id`) like notes and activities, and
gated through the same `EntityAccess` resolver — you cannot attach to, or see
attachments on, a record you cannot read.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
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

#: Malware-scan outcome, tracked separately from `status` because they answer
#: different questions: `status` is "may this be served", `scan_status` is "what
#: did the scanner say". A row can be `available` with the scan still `pending`
#: — see `AttachmentService.finalize` for why that is the deliberate default.
ATTACHMENT_SCAN_STATUSES = ("pending", "clean", "infected", "skipped", "failed")

#: Records a file can hang off. Aligned with EntityAccess's vocabulary so the
#: same readability resolver governs attachments. `note` joined in Phase 3.1:
#: a note is a document in its own right and people expect to attach to it.
ATTACHMENT_ENTITY_TYPES = ("lead", "client", "property", "deal", "task", "note")

#: `s3` covers real S3 and anything that speaks its API (MinIO in development).
#: `memory` is the test/no-Docker adapter and is refused in production.
ATTACHMENT_STORAGE_BACKENDS = ("s3", "memory")


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
    #: The client-declared MIME type until finalization, the *verified* type
    #: after it. Before `available`, treat this as a hint and nothing more.
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)

    #: Populated at finalization from the object storage reports; null until
    #: then, and never taken from the client.
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    storage_backend: Mapped[str] = mapped_column(
        String(20), nullable=False, default="s3", server_default="s3"
    )
    #: The object key the file occupies. Computed deterministically at
    #: registration so the presigned PUT has a target the server chose — a
    #: client never supplies or influences a key beyond its filename.
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending_upload",
        server_default="pending_upload",
    )

    #: When the registration stops being claimable. The presigned PUT is
    #: signed for the same window, so this is a queryable mirror of a fact that
    #: otherwise lives only inside an opaque URL.
    upload_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: When verification passed and the file became servable.
    available_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    scan_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    scanned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Why a file failed or was quarantined, in words a user can act on
    #: ("The stored bytes do not match the declared type"). Shown in the UI, so
    #: it must never carry internal detail.
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

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
            "entity_type IN ('lead', 'client', 'property', 'deal', 'task', 'note')",
            name="ck_attachments_entity_type",
        ),
        CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name="ck_attachments_size",
        ),
        CheckConstraint(
            "scan_status IN ('pending', 'clean', 'infected', 'skipped', 'failed')",
            name="ck_attachments_scan_status",
        ),
        # An available file has bytes behind it, by definition. Without this a
        # bug in the finalize path could publish a row with no size or no key
        # and the download endpoint would sign a URL to nothing.
        CheckConstraint(
            "status <> 'available' OR "
            "(storage_key IS NOT NULL AND size_bytes IS NOT NULL)",
            name="ck_attachments_available_has_object",
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
        # The sweeper's query: abandoned registrations past their window. A
        # partial index keeps it proportional to the backlog rather than to the
        # table, which matters because the backlog is normally empty.
        Index(
            "ix_attachments_abandoned",
            "upload_expires_at",
            postgresql_where=text(
                "status = 'pending_upload' AND deleted_at IS NULL"
            ),
        ),
        # The scan queue's query, for the same reason.
        Index(
            "ix_attachments_scan_pending",
            "created_at",
            postgresql_where=text(
                "scan_status = 'pending' AND deleted_at IS NULL"
            ),
        ),
    )

    def __repr__(self) -> str:
        return f"<Attachment {self.id} {self.status}>"
