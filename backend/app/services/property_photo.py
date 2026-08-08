"""Listing photography.

A photo is not a new kind of record. It is an ordinary attachment filed against
the listing (``entity_type='property'``), which means it already inherits the
storage lifecycle, the malware scan, the tenant isolation and the deletion path
that attachments have had since Phase 3. The only thing the schema could not
express was *which* photo leads, and that is the one column added:
``properties.cover_attachment_id``.

Two decisions worth knowing before changing this file.

**Photos do not go through ``AttachmentService.presign_download``.** That method
writes a ``DOCUMENT_DOWNLOADED`` audit entry, which is exactly right for a
contract a person deliberately saved and exactly wrong for an ``<img>`` the
browser fetched on its own: one listing page would emit a dozen "downloads"
nobody performed, and the audit trail for real documents would drown in them.
Rendering a photo is a read of a record the caller was already shown, so it is
gated like the listing (``properties.view``) and left unaudited.

**The URLs are served inline, not as attachments.** ``presign_get`` forces
``Content-Disposition: attachment`` whenever a filename is passed, because a
stored HTML or SVG file rendering in the storage origin is a stored-XSS
primitive. A photo must render, so no filename is passed — and to keep that
safe, only real raster image types are ever treated as photos here. SVG is not
in the platform's allowlist at all (see ``storage/validation.py``), so the
dangerous case cannot arrive; the check below keeps it that way if the
allowlist ever grows.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models.attachment import Attachment
from app.models.property import Property
from app.services.rbac import AuthorizationContext
from app.services.storage import ObjectStorage, StorageError, get_object_storage

logger = get_logger(__name__)

#: What may be rendered inline. Deliberately narrower than the upload
#: allowlist: `image/tiff` and `image/heic` are legitimate uploads that no
#: browser displays, so serving them as photos would produce a broken card.
PHOTO_CONTENT_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/gif"}
)


class PropertyPhotoService:
    """Reads and presigns a listing's photographs.

    Constructed per request like every other service. `storage` is injectable
    for the same reason `AttachmentService` allows it — a test drives the whole
    path against the in-memory adapter without patching a module global.
    """

    def __init__(
        self,
        session: AsyncSession,
        auth: AuthorizationContext,
        *,
        storage: ObjectStorage | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.auth = auth
        self._storage = storage
        self._settings = settings

    # Resolved on first use, not in the constructor. Half of what this service
    # does — deciding whether a photo may lead a listing — needs neither
    # storage nor configuration, and a caller doing only that should not have
    # to have object storage reachable for the call to be constructible.
    @property
    def storage(self) -> ObjectStorage:
        if self._storage is None:
            self._storage = get_object_storage()
        return self._storage

    @property
    def settings(self) -> Settings:
        if self._settings is None:
            self._settings = get_settings()
        return self._settings

    # ------------------------------------------------------------- reading

    async def list_photos(self, property_id: UUID) -> list[Attachment]:
        """Every servable photo of one listing, cover first, then in set order.

        `sort_order` is the arrangement; `created_at` and the id only break
        ties, so photos added later fall in at the end instead of jumping to
        the front the way an implicit "newest first" would put them.

        The caller must already have been authorised for the listing; this is
        called from `PropertyService`-guarded routes, and the tenant policy on
        `attachments` bounds it regardless.
        """
        query = (
            select(Attachment)
            .where(Attachment.organization_id == self.auth.organization_id)
            .where(Attachment.entity_type == "property")
            .where(Attachment.entity_id == property_id)
            .where(Attachment.deleted_at.is_(None))
            .where(Attachment.status == "available")
            .where(Attachment.content_type.in_(PHOTO_CONTENT_TYPES))
            .order_by(
                Attachment.sort_order.asc(),
                Attachment.created_at.asc(),
                Attachment.id.asc(),
            )
        )
        photos = list((await self.session.execute(query)).scalars().all())

        listing = await self.session.get(Property, property_id)
        cover_id = listing.cover_attachment_id if listing else None
        if cover_id is None:
            return photos
        # Promote the cover without disturbing the rest of the order.
        return sorted(photos, key=lambda photo: photo.id != cover_id)

    async def covers_for(
        self, listings: list[Property]
    ) -> dict[UUID, str]:
        """Cover URLs for a page of listings, in one query and no round trips.

        Presigning is local HMAC arithmetic — signing forty covers costs no
        network at all — so the list endpoint can hand the browser ready-to-use
        URLs instead of making it ask for each one.
        """
        wanted = {
            listing.cover_attachment_id
            for listing in listings
            if listing.cover_attachment_id is not None
        }
        if not wanted:
            return {}

        query = (
            select(Attachment)
            .where(Attachment.organization_id == self.auth.organization_id)
            .where(Attachment.id.in_(wanted))
            .where(Attachment.deleted_at.is_(None))
            .where(Attachment.status == "available")
        )
        by_id = {
            attachment.id: attachment
            for attachment in (await self.session.execute(query)).scalars().all()
        }

        urls: dict[UUID, str] = {}
        for listing in listings:
            attachment = by_id.get(listing.cover_attachment_id)  # type: ignore[arg-type]
            if attachment is None:
                continue
            url = self.view_url(attachment)
            if url is not None:
                urls[listing.id] = url
        return urls

    def view_url(self, attachment: Attachment) -> str | None:
        """A short-lived inline URL, or None if this file cannot be shown.

        Returns None rather than raising: one unrenderable photo must not take
        down the listing page around it. The card falls back to its gradient
        and the rest of the gallery still loads.
        """
        if (
            attachment.storage_key is None
            or attachment.status != "available"
            or attachment.content_type not in PHOTO_CONTENT_TYPES
        ):
            return None
        try:
            presigned = self.storage.presign_get(
                attachment.storage_key,
                expires_in=self.settings.PHOTO_URL_TTL_SECONDS,
                # No filename: that is what keeps the disposition off and the
                # image renderable. See the module docstring.
                content_type=attachment.content_type,
            )
        except StorageError:
            logger.warning(
                "photo_presign_failed",
                extra={"attachment_id": str(attachment.id)},
            )
            return None
        return presigned.url

    # ------------------------------------------------------------- writing

    async def set_cover(self, listing: Property, attachment_id: UUID) -> Property:
        """Promote one of the listing's own photos to the cover.

        The photo must belong to *this* listing: accepting any attachment id
        would let a caller point a listing at a file on a record they cannot
        read, and the presigned URL minted from it would then leak the bytes.

        The caller is responsible for the write authorisation — this is reached
        through `PropertyService._load_for_write`, which is what enforces that a
        listing agent only edits their own.
        """
        photo = await self.session.get(Attachment, attachment_id)
        if (
            photo is None
            or photo.deleted_at is not None
            or photo.organization_id != self.auth.organization_id
            or photo.entity_type != "property"
            or photo.entity_id != listing.id
        ):
            raise NotFoundError("Photo not found on this listing.")
        if photo.status != "available":
            raise ConflictError("This photo is not available yet.")
        if photo.content_type not in PHOTO_CONTENT_TYPES:
            raise ConflictError("This file is not an image that can be displayed.")

        listing.cover_attachment_id = photo.id
        await self.session.flush()
        return listing
