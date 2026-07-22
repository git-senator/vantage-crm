"""Amazon S3 adapter (and anything that speaks the S3 API).

The same adapter serves real S3 and MinIO — the only difference is
`S3_ENDPOINT_URL`, which is set for MinIO in development and left unset in
production. That is the entire portability story, and it is why the local stack
is worth running against MinIO rather than mocking storage: the code path under
test is the production code path.

Two implementation notes that are easy to get wrong:

* **boto3 is synchronous.** Every network call is dispatched to a worker thread;
  a blocking socket read inside an async handler stalls every other request on
  that worker. Presigning is the exception — it is a local HMAC, no I/O — so it
  stays on the loop.
* **Signature V4 with `virtual` addressing breaks MinIO on a bare host.** MinIO
  is reached as `http://minio:9000`, and virtual-host addressing would ask for
  `http://bucket.minio:9000`, which does not resolve. Path addressing is forced
  whenever a custom endpoint is configured.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings
from app.core.logging import get_logger
from app.services.storage.base import (
    ObjectNotFoundError,
    PresignedUrl,
    StorageError,
    StoredObject,
)

logger = get_logger(__name__)

#: Errors that mean the request is wrong, not that the network is. Retrying
#: any of these is guaranteed to fail again and wastes a queue's retry budget.
_TERMINAL_ERRORS = frozenset(
    {
        "AccessDenied",
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
        "NoSuchBucket",
        "InvalidRequest",
        "EntityTooLarge",
        "KeyTooLongError",
    }
)

_NOT_FOUND_ERRORS = frozenset({"404", "NoSuchKey", "NotFound"})


class S3ObjectStorage:
    name = "s3"

    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.S3_BUCKET
        self._sse = settings.S3_SERVER_SIDE_ENCRYPTION
        self._client: Any = self._build_client(settings, settings.S3_ENDPOINT_URL)

        # A second client, bound to the endpoint a browser can actually reach.
        # SigV4 signs the Host header, so a URL minted against `minio:9000`
        # cannot be rewritten to `127.0.0.1:9000` afterwards — the signature
        # would no longer verify. When the two endpoints are the same (real S3,
        # or a MinIO published under one name), this is the same client.
        self._presign_client: Any = (
            self._build_client(settings, settings.S3_PUBLIC_ENDPOINT_URL)
            if settings.S3_PUBLIC_ENDPOINT_URL
            and settings.S3_PUBLIC_ENDPOINT_URL != settings.S3_ENDPOINT_URL
            else self._client
        )

    @staticmethod
    def _build_client(settings: Settings, endpoint_url: str | None) -> Any:
        return boto3.client(
            "s3",
            region_name=settings.S3_REGION,
            endpoint_url=endpoint_url,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID.get_secret_value() or None,
            aws_secret_access_key=(
                settings.S3_SECRET_ACCESS_KEY.get_secret_value() or None
            ),
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path" if endpoint_url else "virtual"},
                retries={"max_attempts": 3, "mode": "adaptive"},
                connect_timeout=5,
                read_timeout=30,
            ),
        )

    # ---------------------------------------------------------- internals

    async def _call(self, operation: str, /, **kwargs: Any) -> Any:
        loop = asyncio.get_running_loop()
        method = getattr(self._client, operation)
        try:
            return await loop.run_in_executor(None, partial(method, **kwargs))
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", "Unknown"))
            if code in _NOT_FOUND_ERRORS:
                raise ObjectNotFoundError(str(kwargs.get("Key", ""))) from exc
            retryable = code not in _TERMINAL_ERRORS
            # The key is logged; object *contents* never are, and a key is
            # composed of ids rather than customer data by construction.
            logger.error(
                "s3_operation_failed",
                extra={
                    "operation": operation,
                    "error_code": code,
                    "retryable": retryable,
                    "key": kwargs.get("Key"),
                },
            )
            raise StorageError(
                f"S3 {operation} failed: {code}", retryable=retryable
            ) from exc
        except BotoCoreError as exc:
            logger.exception("s3_transport_error", extra={"operation": operation})
            raise StorageError("S3 transport failure", retryable=True) from exc

    @staticmethod
    def _expiry(expires_in: int) -> datetime:
        return datetime.now(UTC) + timedelta(seconds=expires_in)

    # ------------------------------------------------------------ presign

    def presign_put(
        self,
        key: str,
        *,
        content_type: str,
        expires_in: int,
        max_bytes: int | None = None,
    ) -> PresignedUrl:
        params: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": key,
            # Signed in, therefore mandatory on the request: a client cannot
            # upload a different type than the one it registered, because the
            # signature would not validate.
            "ContentType": content_type,
        }
        required: dict[str, str] = {"Content-Type": content_type}

        if self._sse:
            params["ServerSideEncryption"] = self._sse
            required["x-amz-server-side-encryption"] = self._sse

        url = self._presign_client.generate_presigned_url(
            "put_object", Params=params, ExpiresIn=expires_in
        )
        # `max_bytes` cannot be bound into a presigned PUT — only a POST policy
        # carries a content-length-range condition, and a browser PUT of raw
        # bytes is the simpler client. The ceiling is enforced at finalization
        # from the size S3 reports, and an oversized object is deleted there.
        return PresignedUrl(
            url=url, expires_at=self._expiry(expires_in), required_headers=required
        )

    def presign_get(
        self,
        key: str,
        *,
        expires_in: int,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> PresignedUrl:
        params: dict[str, Any] = {"Bucket": self._bucket, "Key": key}
        if filename:
            # Always `attachment`. An inline disposition would let a stored
            # HTML or SVG file execute in the storage origin, which is a
            # stored-XSS primitive even though the bucket is private.
            params["ResponseContentDisposition"] = (
                f'attachment; filename="{filename}"'
            )
        if content_type:
            params["ResponseContentType"] = content_type
        url = self._presign_client.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=expires_in
        )
        return PresignedUrl(
            url=url, expires_at=self._expiry(expires_in), required_headers={}
        )

    # ------------------------------------------------------------ objects

    async def head(self, key: str) -> StoredObject:
        response = await self._call("head_object", Bucket=self._bucket, Key=key)
        return StoredObject(
            key=key,
            size_bytes=int(response.get("ContentLength", 0)),
            content_type=response.get("ContentType"),
            etag=str(response.get("ETag", "")).strip('"') or None,
            last_modified=response.get("LastModified"),
        )

    async def read(self, key: str, *, max_bytes: int | None = None) -> bytes:
        kwargs: dict[str, Any] = {"Bucket": self._bucket, "Key": key}
        if max_bytes is not None:
            kwargs["Range"] = f"bytes=0-{max_bytes - 1}"
        response = await self._call("get_object", **kwargs)
        body = response["Body"]
        loop = asyncio.get_running_loop()
        try:
            return bytes(await loop.run_in_executor(None, body.read))
        finally:
            body.close()

    async def stream(
        self, key: str, *, chunk_size: int = 1 << 20
    ) -> AsyncIterator[bytes]:
        """Ranged reads rather than one long-lived body.

        Iterating a botocore stream means blocking `read` calls on a socket held
        open for the whole traversal; ranged GETs keep each thread hop short and
        let a slow consumer stall without pinning a connection.
        """
        offset = 0
        total = (await self.head(key)).size_bytes
        while offset < total:
            end = min(offset + chunk_size, total) - 1
            response = await self._call(
                "get_object",
                Bucket=self._bucket,
                Key=key,
                Range=f"bytes={offset}-{end}",
            )
            body = response["Body"]
            loop = asyncio.get_running_loop()
            try:
                chunk = bytes(await loop.run_in_executor(None, body.read))
            finally:
                body.close()
            if not chunk:
                break
            yield chunk
            offset += len(chunk)

    async def write(self, key: str, data: bytes, *, content_type: str) -> StoredObject:
        kwargs: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": key,
            "Body": data,
            "ContentType": content_type,
        }
        if self._sse:
            kwargs["ServerSideEncryption"] = self._sse
        await self._call("put_object", **kwargs)
        return await self.head(key)

    async def delete(self, key: str) -> None:
        # S3 delete is already idempotent — deleting a missing key returns 204.
        await self._call("delete_object", Bucket=self._bucket, Key=key)

    async def verify_configuration(self) -> bool:
        try:
            await self._call("head_bucket", Bucket=self._bucket)
            return True
        except (StorageError, ObjectNotFoundError):
            logger.exception("s3_configuration_invalid")
            return False
