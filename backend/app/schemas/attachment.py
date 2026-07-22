"""Attachment contracts.

Registration accepts only what a client legitimately knows *before* an upload:
the filename, the declared content type, and where to hang it. Size and checksum
are absent by design — they are read back from storage at finalization, and a
client that could set them would be trusted to describe a file it never sent.

`AttachmentRegistered` is the registration response and is deliberately a
different model from `AttachmentRead`: it carries the presigned PUT, which is a
write credential and belongs in exactly one response rather than on every list
row. `download_url` is likewise minted on request and never included in a list —
a list of 40 files should not mint 40 signed URLs, most of which nobody clicks.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

EntityType = Literal["lead", "client", "property", "deal", "task", "note"]
AttachmentStatus = Literal["pending_upload", "available", "quarantined", "failed"]
ScanStatus = Literal["pending", "clean", "infected", "skipped", "failed"]


class AttachmentUploader(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    full_name: str
    initials: str
    avatar_hue: int


class AttachmentCreate(BaseModel):
    entity_type: EntityType
    entity_id: UUID
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=128)


class AttachmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entity_type: str
    entity_id: UUID
    filename: str
    content_type: str
    size_bytes: int | None
    checksum_sha256: str | None
    status: str
    scan_status: str
    storage_backend: str
    uploader: AttachmentUploader | None
    #: Set only while the row is claimable; the presigned PUT is signed for the
    #: same instant, so a client can show a countdown without a second call.
    upload_expires_at: datetime | None = None
    available_at: datetime | None = None
    #: Why a `failed` or `quarantined` file is not being served. User-facing.
    failure_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class PresignedUpload(BaseModel):
    """A short-lived credential to write exactly one object.

    `required_headers` is not advisory: the signature covers them, so a PUT that
    omits `Content-Type` is rejected by storage rather than by us. Clients must
    send them verbatim.
    """

    url: str
    expires_at: datetime
    required_headers: dict[str, str]
    max_bytes: int


class AttachmentRegistered(BaseModel):
    """Registration response: the row, plus where to put the bytes."""

    attachment: AttachmentRead
    upload: PresignedUpload


class PresignedDownload(BaseModel):
    url: str
    expires_at: datetime
    filename: str


class AttachmentFinalize(BaseModel):
    """Optional client-side integrity claim.

    If supplied, the checksum the client computed over what it *sent* must match
    the one the server computes over what was *stored*. A mismatch means the
    bytes changed in flight, and the upload is rejected rather than published.

    Optional because a browser can compute SHA-256 over a large file only at
    real cost, and the server's own verification does not depend on it.
    """

    checksum_sha256: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
