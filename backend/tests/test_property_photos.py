"""Listing photography.

Photos reuse the attachment machinery rather than adding a table, so what is
worth pinning down is not "can a file be stored" — `test_attachments.py` covers
that — but the three things this feature added on top:

  * **Order.** The gallery is what someone arranged, cover first. Photos
    written in one transaction share `created_at` to the microsecond, so an
    implementation that leans on the timestamp alone passes by luck and
    reshuffles the moment the tie is broken on a different UUID.
  * **Reach.** `set_cover` takes an attachment id from the caller. If it did
    not insist the photo belongs to *this* listing, a caller could point their
    own listing at a file on a record they cannot read and then have the server
    sign a URL for it. `TestCoverCannotReachOtherRecords` is that test.
  * **Fragility.** One unrenderable photo must not take down the page around
    it, because the page is the listing and the photo is decoration.

Storage is the in-memory adapter for the same reason as the attachment tests:
it issues genuine signed URLs, so the assertions are about behaviour.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.attachment import Attachment
from app.schemas.lead import LeadCreate
from app.schemas.property import PropertyCreate
from app.services.lead import LeadService
from app.services.property import PropertyService
from app.services.property_photo import PropertyPhotoService
from app.services.storage.memory import InMemoryObjectStorage

pytestmark = pytest.mark.integration

WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 "


def _payload(**overrides: object) -> PropertyCreate:
    data: dict = {
        "title": "Villa Barra da Lagoa",
        "address_line1": "Barra da Lagoa",
        "city": "Florianópolis",
        "state": "SC",
        "postal_code": "88061",
        "property_type": "single_family",
        "price": Decimal("1770000.00"),
        **overrides,
    }
    return PropertyCreate(**data)


async def _attach(
    db: AsyncSession,
    storage: InMemoryObjectStorage,
    *,
    organization_id,
    entity_id,
    filename: str,
    sort_order: int,
    entity_type: str = "property",
    content_type: str = "image/webp",
    status: str = "available",
) -> Attachment:
    """A published photo, written the way the importer writes one."""
    attachment = Attachment(
        organization_id=organization_id,
        entity_type=entity_type,
        entity_id=entity_id,
        filename=filename,
        content_type=content_type,
        size_bytes=len(WEBP),
        storage_backend=storage.name,
        status="pending_upload",
        scan_status="skipped",
        sort_order=sort_order,
    )
    db.add(attachment)
    await db.flush()
    attachment.storage_key = (
        f"org/{organization_id}/{entity_type}/{entity_id}"
        f"/{attachment.id}/{filename}"
    )
    await storage.write(attachment.storage_key, WEBP, content_type=content_type)
    attachment.status = status
    attachment.available_at = datetime.now(UTC)
    await db.flush()
    return attachment


@pytest.fixture
async def listing(db: AsyncSession, admin):  # type: ignore[no-untyped-def]
    user, auth = admin
    return await PropertyService(db, auth).create_property(_payload(), user)


class TestGalleryOrder:
    async def test_photos_come_back_in_the_order_they_were_arranged(
        self, db: AsyncSession, admin, listing, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        _user, auth = admin
        for index, name in enumerate(["a.webp", "b.webp", "c.webp"]):
            await _attach(
                db,
                object_storage,
                organization_id=auth.organization_id,
                entity_id=listing.id,
                filename=name,
                sort_order=index,
            )

        photos = await PropertyPhotoService(
            db, auth, storage=object_storage, settings=storage_settings
        ).list_photos(listing.id)

        assert [photo.filename for photo in photos] == ["a.webp", "b.webp", "c.webp"]

    async def test_the_cover_leads_wherever_it_sits_in_the_set(
        self, db: AsyncSession, admin, listing, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        photos = [
            await _attach(
                db,
                object_storage,
                organization_id=auth.organization_id,
                entity_id=listing.id,
                filename=name,
                sort_order=index,
            )
            for index, name in enumerate(["a.webp", "b.webp", "c.webp"])
        ]

        await PropertyService(db, auth).set_cover(listing.id, photos[2].id, user)

        ordered = await PropertyPhotoService(
            db, auth, storage=object_storage, settings=storage_settings
        ).list_photos(listing.id)

        # Cover first, and the rest keep their arrangement rather than closing
        # ranks in some new order.
        assert [photo.filename for photo in ordered] == [
            "c.webp",
            "a.webp",
            "b.webp",
        ]

    async def test_a_pending_photo_is_not_servable(
        self, db: AsyncSession, admin, listing, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        _user, auth = admin
        await _attach(
            db,
            object_storage,
            organization_id=auth.organization_id,
            entity_id=listing.id,
            filename="half-uploaded.webp",
            sort_order=0,
            status="pending_upload",
        )

        photos = await PropertyPhotoService(
            db, auth, storage=object_storage, settings=storage_settings
        ).list_photos(listing.id)

        assert photos == []


class TestCovers:
    async def test_cover_url_is_minted_for_a_page_of_listings(
        self, db: AsyncSession, admin, listing, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        photo = await _attach(
            db,
            object_storage,
            organization_id=auth.organization_id,
            entity_id=listing.id,
            filename="cover.webp",
            sort_order=0,
        )
        await PropertyService(db, auth).set_cover(listing.id, photo.id, user)

        bare = await PropertyService(db, auth).create_property(
            _payload(title="No photographs yet"), user
        )
        covers = await PropertyPhotoService(
            db, auth, storage=object_storage, settings=storage_settings
        ).covers_for([listing, bare])

        assert object_storage.resolve(covers[listing.id], "get") == photo.storage_key
        # A listing with no cover is absent, not present-and-null: the card
        # falls back to its gradient.
        assert bare.id not in covers

    async def test_a_deleted_cover_leaves_the_listing_standing(
        self, db: AsyncSession, admin, listing, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        photo = await _attach(
            db,
            object_storage,
            organization_id=auth.organization_id,
            entity_id=listing.id,
            filename="cover.webp",
            sort_order=0,
        )
        await PropertyService(db, auth).set_cover(listing.id, photo.id, user)

        photo.deleted_at = datetime.now(UTC)
        await db.flush()

        covers = await PropertyPhotoService(
            db, auth, storage=object_storage, settings=storage_settings
        ).covers_for([listing])
        assert covers == {}


class TestCoverCannotReachOtherRecords:
    """The check that keeps a signed URL from becoming a way to read a file."""

    async def test_a_photo_on_another_listing_is_refused(
        self, db: AsyncSession, admin, listing, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        other = await PropertyService(db, auth).create_property(
            _payload(title="Someone else's listing"), user
        )
        stranger = await _attach(
            db,
            object_storage,
            organization_id=auth.organization_id,
            entity_id=other.id,
            filename="theirs.webp",
            sort_order=0,
        )

        with pytest.raises(NotFoundError):
            await PropertyService(db, auth).set_cover(listing.id, stranger.id, user)

    async def test_a_document_on_a_lead_is_refused(
        self, db: AsyncSession, admin, listing, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        """The id is a plain UUID, so nothing about its shape says "photo"."""
        user, auth = admin
        lead = await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Sana", last_name="Kaur"), user
        )
        elsewhere = await _attach(
            db,
            object_storage,
            organization_id=auth.organization_id,
            entity_id=lead.id,
            entity_type="lead",
            filename="passport-scan.webp",
            sort_order=0,
        )

        with pytest.raises(NotFoundError):
            await PropertyService(db, auth).set_cover(listing.id, elsewhere.id, user)

    async def test_an_unknown_id_is_refused(
        self, db: AsyncSession, admin, listing
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        with pytest.raises(NotFoundError):
            await PropertyService(db, auth).set_cover(listing.id, uuid4(), user)

    async def test_a_non_image_cannot_become_a_cover(
        self, db: AsyncSession, admin, listing, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        contract = await _attach(
            db,
            object_storage,
            organization_id=auth.organization_id,
            entity_id=listing.id,
            filename="contract.pdf",
            sort_order=0,
            content_type="application/pdf",
        )

        with pytest.raises(ConflictError):
            await PropertyService(db, auth).set_cover(listing.id, contract.id, user)


class TestRenderability:
    async def test_a_photo_with_no_bytes_yields_no_url(
        self, db: AsyncSession, admin, listing, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        """Returning None rather than raising is the point: one broken photo
        must not take the listing page down with it."""
        _user, auth = admin
        orphan = Attachment(
            organization_id=auth.organization_id,
            entity_type="property",
            entity_id=listing.id,
            filename="never-arrived.webp",
            content_type="image/webp",
            storage_backend=object_storage.name,
            status="pending_upload",
            scan_status="pending",
        )
        db.add(orphan)
        await db.flush()

        service = PropertyPhotoService(
            db, auth, storage=object_storage, settings=storage_settings
        )
        assert service.view_url(orphan) is None
