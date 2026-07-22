"""Transport-agnostic object-storage contract.

Business logic depends on `ObjectStorage` — never on boto3, a bucket name, or
an S3 client type. This is the same shape as `app/services/notifications`: a
structural `Protocol`, so an adapter needs no base class and a test can pass a
plain fake.

**Why the interface is presign-first.** The obvious design is `put(key, bytes)`
and `get(key) -> bytes`, and it is wrong here: it routes every byte of every
file through the API process, which turns a 50 MB upload into 50 MB of memory
and one occupied worker for the duration of a client's slow connection. The
browser talks to object storage directly over a short-lived signed URL, and the
API only ever handles metadata. `read`/`write` exist as well, but for the
*server's* own small reads — the post-upload verification needs the first few
kilobytes to sniff magic bytes, and the checksum needs a stream — not for
serving user traffic.

**Why `head` matters.** After a presigned PUT the API has no idea whether the
client actually uploaded anything, and it cannot ask the client. `head` is how
finalization establishes the truth: the real size and the real ETag, read from
storage rather than declared by the uploader.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class StoredObject:
    """What storage says about an object that exists."""

    key: str
    size_bytes: int
    content_type: str | None
    etag: str | None
    last_modified: datetime | None


@dataclass(frozen=True, slots=True)
class PresignedUrl:
    url: str
    expires_at: datetime
    #: Headers the client MUST send with the request for the signature to
    #: validate. Empty for a plain GET; a PUT signed with a content type will
    #: reject an upload that omits it.
    required_headers: dict[str, str]


class StorageError(Exception):
    """An object-storage operation failed.

    `retryable` distinguishes a transient network or throttling fault from a
    permanent one (missing bucket, denied credentials) so the queue in Phase
    3.2 does not burn its retry budget on a request that can never succeed.
    """

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class ObjectNotFoundError(StorageError):
    """No object at that key. Never retryable — it will not appear on its own."""

    def __init__(self, key: str) -> None:
        super().__init__(f"No object at key: {key}", retryable=False)
        self.key = key


@runtime_checkable
class ObjectStorage(Protocol):
    """Implemented by every storage adapter."""

    name: str

    def presign_put(
        self,
        key: str,
        *,
        content_type: str,
        expires_in: int,
        max_bytes: int | None = None,
    ) -> PresignedUrl:
        """A short-lived URL the client may PUT exactly one object to.

        `max_bytes` is advisory at this layer — S3 cannot enforce a size ceiling
        on a plain presigned PUT, only on a POST policy. The real enforcement is
        the `head` check at finalization, which refuses to mark an oversized
        object available and deletes it. Adapters that *can* enforce it should.
        """
        ...

    def presign_get(
        self,
        key: str,
        *,
        expires_in: int,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> PresignedUrl:
        """A short-lived download URL.

        `filename` is served back as a `Content-Disposition: attachment`
        override so the browser saves the user's original name rather than the
        opaque storage key — and so a text/html file cannot render in the
        storage origin.
        """
        ...

    async def head(self, key: str) -> StoredObject:
        """Object metadata. Raises `ObjectNotFoundError` when absent."""
        ...

    async def read(self, key: str, *, max_bytes: int | None = None) -> bytes:
        """Read an object (or its first `max_bytes`) into memory.

        Server-side use only — magic-byte sniffing and small-file work. Never
        call this to serve a download; that is what `presign_get` is for.
        """
        ...

    # Not `async def`: an async-generator function returns its iterator without
    # being awaited, so the callable itself is synchronous and the iteration is
    # what suspends. Declaring it `async def` here would require callers to
    # write `async for chunk in await storage.stream(...)`.
    def stream(self, key: str, *, chunk_size: int = 1 << 20) -> AsyncIterator[bytes]:
        """Iterate an object's bytes. Used for checksumming without buffering."""
        ...

    async def write(self, key: str, data: bytes, *, content_type: str) -> StoredObject:
        """Put an object from the server. Used by tests, seeds and job output."""
        ...

    async def delete(self, key: str) -> None:
        """Remove an object. Succeeds silently when the key does not exist —
        deletion is idempotent, and a retry of a successful delete is not an
        error."""
        ...

    async def verify_configuration(self) -> bool:
        """Cheap reachability/credential check for the readiness probe."""
        ...
