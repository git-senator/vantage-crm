"""Idempotency keys for public write operations.

A machine that POSTs, does not hear back (a dropped connection, a timeout), and
retries must not create a second record. The client sends an `Idempotency-Key`
header on a write; the first request runs and its response is stored under that
key, and any replay returns the stored response instead of executing again.

Design notes:

  * **Keyed to the credential, not just the header.** The stored key is
    `idem:{api-key-hash}:{method}:{path}:{idempotency-key}`. Two tenants — or
    two keys — that happen to pick the same idempotency string never collide,
    and a replay only matches the credential that issued the original.
  * **Concurrent duplicates get 409, not a double-execute.** The key is claimed
    with `SET NX` before the handler runs. A second request arriving while the
    first is still in flight sees the *processing* marker and is refused rather
    than racing it.
  * **Server errors are not cached.** A 5xx clears the claim so the client may
    retry; a 4xx is cached like a success, because replaying a deterministic
    client error is correct and cheap.
  * **Opt-in.** A write without the header behaves exactly as before, so this is
    backward compatible for callers that do not use it.
  * **Fails open.** If Redis is unavailable the write proceeds unprotected,
    matching the rate limiter — a cache outage must not take writes down.

Idempotency is enforced as middleware on the public sub-app so every write
route inherits it without repeating the dance, the same shape the broad rate
limiter takes.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.exceptions import CONTENT_TYPE, PROBLEM_BASE
from app.core.logging import get_logger, request_id_var
from app.core.middleware import _api_key_credential
from app.core.redis import get_redis

logger = get_logger(__name__)

HEADER = "Idempotency-Key"
REPLAY_HEADER = "Idempotency-Replayed"

#: Long enough that a client's retry window is covered, short enough that the
#: store does not grow without bound. A day comfortably covers a retrying job.
_TTL_SECONDS = 24 * 60 * 60
_MAX_KEY_LENGTH = 255
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_PROCESSING = "\x00processing"


def _problem(status_code: int, problem_type: str, title: str, detail: str) -> JSONResponse:
    body: dict[str, object] = {
        "type": f"{PROBLEM_BASE}/{problem_type}",
        "title": title,
        "status": status_code,
        "detail": detail,
    }
    if (request_id := request_id_var.get()) is not None:
        body["request_id"] = request_id
    return JSONResponse(status_code=status_code, content=body, media_type=CONTENT_TYPE)


class IdempotencyMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method not in _WRITE_METHODS:
            return await call_next(request)

        idem_key = request.headers.get(HEADER)
        if not idem_key:
            return await call_next(request)

        if len(idem_key) > _MAX_KEY_LENGTH:
            return _problem(
                400,
                "invalid-idempotency-key",
                "Invalid idempotency key",
                f"The {HEADER} header must be at most {_MAX_KEY_LENGTH} characters.",
            )

        credential = _api_key_credential(request)
        if credential is None:
            # No key on the request. Authentication will reject it in a moment;
            # there is nothing to scope an idempotency record to until then.
            return await call_next(request)

        redis_key = f"idem:{credential}:{request.method}:{request.url.path}:{idem_key}"

        try:
            claimed = await get_redis().set(
                redis_key, _PROCESSING, nx=True, ex=_TTL_SECONDS
            )
        except Exception:
            logger.warning("idempotency_backend_unavailable")
            return await call_next(request)  # fail open

        if not claimed:
            return await self._handle_existing(redis_key, idem_key)

        return await self._run_and_store(request, call_next, redis_key, idem_key)

    async def _handle_existing(self, redis_key: str, idem_key: str) -> Response:
        """A record already exists for this key: replay it, or refuse a race."""
        try:
            stored = await get_redis().get(redis_key)
        except Exception:
            logger.warning("idempotency_backend_unavailable")
            return _problem(
                409,
                "idempotency-conflict",
                "Idempotency conflict",
                "A request with this Idempotency-Key is already in progress.",
            )

        if stored is None or stored == _PROCESSING:
            return _problem(
                409,
                "idempotency-conflict",
                "Idempotency conflict",
                "A request with this Idempotency-Key is already in progress.",
            )

        record = json.loads(stored)
        response = Response(
            content=base64.b64decode(record["body"]),
            status_code=record["status"],
            media_type=record.get("media_type"),
        )
        for name, value in record.get("headers", {}).items():
            response.headers[name] = value
        response.headers[REPLAY_HEADER] = "true"
        response.headers[HEADER] = idem_key
        return response

    async def _run_and_store(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
        redis_key: str,
        idem_key: str,
    ) -> Response:
        try:
            response = await call_next(request)
        except Exception:
            # Never leave a claim behind for a request that crashed — the client
            # must be able to retry.
            await self._release(redis_key)
            raise

        # BaseHTTPMiddleware hands downstream responses back as a streaming
        # response, so the body must be drained from the iterator and re-sent.
        body = b"".join(
            [chunk async for chunk in response.body_iterator]  # type: ignore[attr-defined]
        )

        if response.status_code >= 500:
            # Transient by assumption: drop the claim so a retry can succeed.
            await self._release(redis_key)
        else:
            record = json.dumps(
                {
                    "status": response.status_code,
                    "media_type": response.media_type,
                    "headers": {
                        name: value
                        for name, value in response.headers.items()
                        # Location is worth replaying; hop-by-hop and length
                        # headers are recomputed when the body is re-sent.
                        if name.lower() in ("content-type", "location")
                    },
                    "body": base64.b64encode(body).decode(),
                }
            )
            try:
                await get_redis().set(redis_key, record, ex=_TTL_SECONDS)
            except Exception:
                logger.warning("idempotency_store_failed")

        rebuilt = Response(
            content=body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )
        rebuilt.headers[HEADER] = idem_key
        return rebuilt

    @staticmethod
    async def _release(redis_key: str) -> None:
        try:
            await get_redis().delete(redis_key)
        except Exception:
            logger.warning("idempotency_release_failed")
