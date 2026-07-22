"""In-process object storage.

Two jobs, and the second is the one that justifies the file:

1. **Tests without Docker.** The unit suite must be able to exercise the whole
   attachment lifecycle — register, upload, finalize, download, delete — on a
   laptop with no MinIO running. Storage is the one dependency of that flow that
   is not the database.
2. **Presigned URLs that actually behave like presigned URLs.** A fake that
   returns `"http://example/whatever"` proves nothing about expiry or tampering,
   and "presigned URLs expire correctly and are not reusable" is a Phase 3 exit
   criterion. So this issues a real HMAC over (key, operation, expiry) and
   `resolve` verifies it: an expired URL fails, an edited key fails, and a URL
   minted for one object cannot be pointed at another.

Not for production. `assert_production_ready` refuses to start with it.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, quote, urlparse

from app.services.storage.base import (
    ObjectNotFoundError,
    PresignedUrl,
    StorageError,
    StoredObject,
)

_BASE = "memory://vantage-documents"


@dataclass(slots=True)
class _Entry:
    data: bytes
    content_type: str
    last_modified: datetime


class SignatureError(StorageError):
    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


class InMemoryObjectStorage:
    name = "memory"

    def __init__(self) -> None:
        self._objects: dict[str, _Entry] = {}
        self._secret = secrets.token_bytes(32)

    # ---------------------------------------------------------- signing

    def _sign(self, key: str, operation: str, expires_at: int) -> str:
        payload = f"{operation}:{key}:{expires_at}".encode()
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()

    def _presign(self, key: str, operation: str, expires_in: int) -> PresignedUrl:
        expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
        stamp = int(expires_at.timestamp())
        signature = self._sign(key, operation, stamp)
        url = (
            f"{_BASE}/{quote(key)}"
            f"?op={operation}&expires={stamp}&signature={signature}"
        )
        return PresignedUrl(url=url, expires_at=expires_at, required_headers={})

    def resolve(self, url: str, operation: str) -> str:
        """Verify a URL this adapter issued and return the key it authorises.

        Raises rather than returning a boolean: every caller of this is a test
        asserting on the failure mode, and a silent `False` would be easy to
        write a passing test around.
        """
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        key = parsed.path.lstrip("/")
        # `memory://vantage-documents/...` puts the bucket in netloc.
        try:
            stamp = int(query["expires"][0])
            signature = query["signature"][0]
            signed_operation = query["op"][0]
        except (KeyError, IndexError, ValueError) as exc:
            raise SignatureError("Malformed presigned URL.") from exc

        if signed_operation != operation:
            raise SignatureError(
                f"URL authorises {signed_operation}, not {operation}."
            )
        if not hmac.compare_digest(
            signature, self._sign(key, signed_operation, stamp)
        ):
            raise SignatureError("Presigned URL signature does not verify.")
        if datetime.now(UTC).timestamp() > stamp:
            raise SignatureError("Presigned URL has expired.")
        return key

    # ---------------------------------------------------------- protocol

    def presign_put(
        self,
        key: str,
        *,
        content_type: str,
        expires_in: int,
        max_bytes: int | None = None,
    ) -> PresignedUrl:
        presigned = self._presign(key, "put", expires_in)
        return PresignedUrl(
            url=presigned.url,
            expires_at=presigned.expires_at,
            required_headers={"Content-Type": content_type},
        )

    def presign_get(
        self,
        key: str,
        *,
        expires_in: int,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> PresignedUrl:
        return self._presign(key, "get", expires_in)

    async def head(self, key: str) -> StoredObject:
        entry = self._objects.get(key)
        if entry is None:
            raise ObjectNotFoundError(key)
        return StoredObject(
            key=key,
            size_bytes=len(entry.data),
            content_type=entry.content_type,
            etag=hashlib.md5(entry.data, usedforsecurity=False).hexdigest(),
            last_modified=entry.last_modified,
        )

    async def read(self, key: str, *, max_bytes: int | None = None) -> bytes:
        entry = self._objects.get(key)
        if entry is None:
            raise ObjectNotFoundError(key)
        return entry.data if max_bytes is None else entry.data[:max_bytes]

    async def stream(
        self, key: str, *, chunk_size: int = 1 << 20
    ) -> AsyncIterator[bytes]:
        entry = self._objects.get(key)
        if entry is None:
            raise ObjectNotFoundError(key)
        for offset in range(0, len(entry.data), chunk_size):
            yield entry.data[offset : offset + chunk_size]

    async def write(self, key: str, data: bytes, *, content_type: str) -> StoredObject:
        self._objects[key] = _Entry(
            data=data, content_type=content_type, last_modified=datetime.now(UTC)
        )
        return await self.head(key)

    async def delete(self, key: str) -> None:
        self._objects.pop(key, None)

    async def verify_configuration(self) -> bool:
        return True

    # ------------------------------------------------------------ testing

    def keys(self) -> list[str]:
        return sorted(self._objects)

    def clear(self) -> None:
        self._objects.clear()
