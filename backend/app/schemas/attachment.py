"""Attachment contracts.

Registration accepts only what a client legitimately knows *before* an upload:
the filename, the declared content type, and where to hang it. Size and
checksum are absent — they come from the bytes, in Phase 3, and a client that
could set them would be trusted to describe a file it had not sent.

`upload_url` on the read model is the seam for Phase 3's presigned PUT. It is
`None` today, and the `status` says why (`pending_upload`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

EntityType = Literal["lead", "client", "property", "deal", "task"]
AttachmentStatus = Literal["pending_upload", "available", "quarantined", "failed"]


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
    status: str
    storage_backend: str
    uploader: AttachmentUploader | None
    #: Phase 3 fills this with a short-lived presigned PUT. Null while the
    #: storage layer does not exist — the status explains the absence.
    upload_url: str | None = None
    #: Phase 3 fills this with a short-lived presigned GET, and only for
    #: `available` rows. Null today.
    download_url: str | None = None
    created_at: datetime
    updated_at: datetime
