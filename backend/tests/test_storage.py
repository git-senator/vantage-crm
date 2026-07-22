"""Storage layer — keys, content verification, and presigned-URL behaviour.

Pure unit tests: no database, no Docker, no MinIO. Everything here is about the
three things that go wrong with file storage and are invisible until they are
exploited — a filename that steers the key, bytes that are not what they claim,
and a signed URL that outlives its purpose.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from app.services.storage.base import ObjectNotFoundError
from app.services.storage.keys import (
    build_storage_key,
    key_belongs_to_organization,
    sanitize_filename,
)
from app.services.storage.memory import InMemoryObjectStorage, SignatureError
from app.services.storage.validation import (
    is_allowed_content_type,
    normalise_content_type,
    verify_content,
)

PDF = b"%PDF-1.7\n1 0 obj\n<</Type/Catalog>>\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class TestFilenameSanitisation:
    @pytest.mark.parametrize(
        "raw",
        [
            "../../etc/passwd",
            "..\\..\\windows\\system32\\config",
            "/absolute/path/secret.pdf",
            "....//....//escape.pdf",
        ],
    )
    def test_path_traversal_cannot_steer_the_key(self, raw: str) -> None:
        """The whole point of the key layout is that only the last component is
        user-influenced. A filename that reintroduces a separator would undo it."""
        cleaned = sanitize_filename(raw)
        assert "/" not in cleaned
        assert "\\" not in cleaned
        assert ".." not in cleaned

    def test_accents_are_transliterated_not_deleted(self) -> None:
        assert sanitize_filename("résumé.pdf") == "resume.pdf"

    def test_ordinary_names_survive_intact(self) -> None:
        assert sanitize_filename("Offer-2026_final.v2.pdf") == "Offer-2026_final.v2.pdf"

    def test_windows_reserved_stems_are_defused(self) -> None:
        assert sanitize_filename("CON.pdf") != "CON.pdf"
        assert sanitize_filename("CON.pdf").endswith(".pdf")

    def test_long_names_keep_their_extension(self) -> None:
        cleaned = sanitize_filename("a" * 400 + ".pdf")
        assert len(cleaned) <= 200
        assert cleaned.endswith(".pdf")

    def test_a_name_of_only_junk_still_yields_a_component(self) -> None:
        # An empty component would produce a key ending in "/", which is a
        # directory marker in every S3 console and not an object.
        assert sanitize_filename("///...///") == "file"


class TestStorageKeys:
    def test_key_is_tenant_prefixed_and_unique_per_attachment(self) -> None:
        org = uuid4()
        entity = uuid4()
        first = build_storage_key(
            organization_id=org,
            entity_type="deal",
            entity_id=entity,
            attachment_id=uuid4(),
            filename="contract.pdf",
        )
        second = build_storage_key(
            organization_id=org,
            entity_type="deal",
            entity_id=entity,
            attachment_id=uuid4(),
            filename="contract.pdf",
        )
        assert first.startswith(f"org/{org}/")
        # Same record, same filename, different object: a re-upload must not
        # overwrite an already-verified file.
        assert first != second

    def test_tenant_check_rejects_another_organizations_prefix(self) -> None:
        mine, theirs = uuid4(), uuid4()
        key = build_storage_key(
            organization_id=theirs,
            entity_type="lead",
            entity_id=uuid4(),
            attachment_id=uuid4(),
            filename="x.pdf",
        )
        assert key_belongs_to_organization(key, theirs)
        assert not key_belongs_to_organization(key, mine)

    def test_prefix_check_is_not_fooled_by_a_shared_string_start(self) -> None:
        org = uuid4()
        # A key under a *different* org whose id merely starts with the same
        # characters must not pass. The trailing slash in the prefix is what
        # makes that true, so it is worth a test.
        assert not key_belongs_to_organization(f"org/{org}extra/lead/x", org)


class TestContentVerification:
    def test_declared_and_actual_agree(self) -> None:
        assert verify_content("application/pdf", PDF).ok
        assert verify_content("image/png", PNG).ok

    def test_content_type_parameters_are_ignored(self) -> None:
        assert normalise_content_type("Text/CSV; charset=utf-8") == "text/csv"
        assert verify_content("Application/PDF", PDF).ok

    def test_an_executable_renamed_to_pdf_is_rejected(self) -> None:
        verdict = verify_content("application/pdf", b"MZ\x90\x00" + b"\x00" * 64)
        assert not verdict.ok
        assert "executable" in (verdict.reason or "").lower()

    def test_html_declared_as_text_is_rejected(self) -> None:
        """The stored-XSS path: a browser that sniffs the download would render
        this in the storage origin."""
        verdict = verify_content("text/plain", b"<!DOCTYPE html><script>alert(1)</script>")
        assert not verdict.ok

    def test_svg_declared_as_text_is_rejected(self) -> None:
        verdict = verify_content("text/plain", b"<svg xmlns='http://www.w3.org/2000/svg'>")
        assert not verdict.ok

    def test_binary_declared_as_text_is_rejected(self) -> None:
        assert not verify_content("text/plain", b"\x00\x01\x02\xff\xfe").ok

    def test_genuine_text_passes(self) -> None:
        assert verify_content("text/csv", b"name,email\nSana,sana@example.com\n").ok

    def test_wrong_family_is_rejected(self) -> None:
        verdict = verify_content("image/png", PDF)
        assert not verdict.ok
        assert "do not match" in (verdict.reason or "")

    def test_riff_that_is_not_webp_is_rejected(self) -> None:
        # RIFF alone is also WAV and AVI; the brand at offset 8 decides.
        assert not verify_content("image/webp", b"RIFF\x00\x00\x00\x00WAVE").ok
        assert verify_content("image/webp", b"RIFF\x00\x00\x00\x00WEBP").ok

    def test_an_empty_object_is_rejected(self) -> None:
        assert not verify_content("application/pdf", b"").ok

    def test_the_allowlist_is_an_allowlist(self) -> None:
        assert is_allowed_content_type("application/pdf")
        assert not is_allowed_content_type("application/x-msdownload")
        assert not is_allowed_content_type("image/svg+xml")  # scriptable
        assert not is_allowed_content_type("text/html")


class TestInMemoryPresigning:
    """The memory adapter's URLs are real HMACs, so these assertions are about
    presigning semantics rather than about the fake."""

    async def test_a_signed_url_resolves_to_its_key(self) -> None:
        storage = InMemoryObjectStorage()
        presigned = storage.presign_put(
            "org/a/lead/b/c/x.pdf", content_type="application/pdf", expires_in=60
        )
        assert storage.resolve(presigned.url, "put") == "org/a/lead/b/c/x.pdf"

    async def test_an_expired_url_is_refused(self) -> None:
        storage = InMemoryObjectStorage()
        presigned = storage.presign_get("k", expires_in=1)
        await asyncio.sleep(1.1)
        with pytest.raises(SignatureError, match="expired"):
            storage.resolve(presigned.url, "get")

    async def test_a_url_cannot_be_repointed_at_another_key(self) -> None:
        """The signature covers the key, so editing the path invalidates it —
        this is what stops one file's download URL becoming another's."""
        storage = InMemoryObjectStorage()
        presigned = storage.presign_get("org/a/lead/b/c/mine.pdf", expires_in=60)
        tampered = presigned.url.replace("mine.pdf", "theirs.pdf")
        with pytest.raises(SignatureError, match="signature"):
            storage.resolve(tampered, "get")

    async def test_a_download_url_does_not_authorise_an_upload(self) -> None:
        storage = InMemoryObjectStorage()
        presigned = storage.presign_get("k", expires_in=60)
        with pytest.raises(SignatureError, match="authorises"):
            storage.resolve(presigned.url, "put")

    async def test_put_requires_the_signed_content_type_header(self) -> None:
        storage = InMemoryObjectStorage()
        presigned = storage.presign_put(
            "k", content_type="application/pdf", expires_in=60
        )
        assert presigned.required_headers["Content-Type"] == "application/pdf"


class TestInMemoryObjects:
    async def test_write_head_read_roundtrip(self) -> None:
        storage = InMemoryObjectStorage()
        stored = await storage.write("k", PDF, content_type="application/pdf")
        assert stored.size_bytes == len(PDF)
        assert await storage.read("k", max_bytes=5) == PDF[:5]

    async def test_head_of_a_missing_object_raises_and_is_not_retryable(self) -> None:
        storage = InMemoryObjectStorage()
        with pytest.raises(ObjectNotFoundError) as caught:
            await storage.head("nope")
        assert caught.value.retryable is False

    async def test_delete_is_idempotent(self) -> None:
        storage = InMemoryObjectStorage()
        await storage.write("k", PDF, content_type="application/pdf")
        await storage.delete("k")
        await storage.delete("k")  # must not raise — a retried delete is normal
        assert storage.keys() == []

    async def test_stream_reassembles_the_object(self) -> None:
        storage = InMemoryObjectStorage()
        payload = PDF * 500
        await storage.write("k", payload, content_type="application/pdf")
        chunks = [chunk async for chunk in storage.stream("k", chunk_size=1024)]
        assert len(chunks) > 1
        assert b"".join(chunks) == payload
