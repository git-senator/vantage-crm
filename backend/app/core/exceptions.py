"""Application errors and RFC 7807 problem+json handlers.

Clients receive a stable machine-readable `type` URI and a safe message.
Unhandled exceptions return the request's correlation ID and nothing else — the
stack trace goes to the log, never over the wire (OWASP A05).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, request_id_var

logger = get_logger(__name__)

PROBLEM_BASE = "https://vantage.crm/problems"
CONTENT_TYPE = "application/problem+json"


class AppError(Exception):
    """Base for expected, client-facing failures."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    problem_type: str = "application-error"
    title: str = "Application error"

    def __init__(self, detail: str | None = None, **extra: Any) -> None:
        self.detail = detail or self.title
        self.extra = extra
        super().__init__(self.detail)


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    problem_type = "not-found"
    title = "Resource not found"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    problem_type = "conflict"
    title = "Resource conflict"


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    problem_type = "authentication-failed"
    title = "Authentication failed"


class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    problem_type = "permission-denied"
    title = "Permission denied"


class RateLimitedError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    problem_type = "rate-limited"
    title = "Too many requests"


def _problem(
    *,
    status_code: int,
    problem_type: str,
    title: str,
    detail: str,
    instance: str | None = None,
    **extra: Any,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"{PROBLEM_BASE}/{problem_type}",
        "title": title,
        "status": status_code,
        "detail": detail,
    }
    if instance:
        body["instance"] = instance
    if (request_id := request_id_var.get()) is not None:
        body["request_id"] = request_id
    body.update(extra)
    return JSONResponse(status_code=status_code, content=body, media_type=CONTENT_TYPE)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        # 401/403 are security-relevant: log them for the audit trail.
        if exc.status_code in (401, 403):
            logger.warning(
                "authorization_failure",
                extra={"path": request.url.path, "problem": exc.problem_type},
            )
        return _problem(
            status_code=exc.status_code,
            problem_type=exc.problem_type,
            title=exc.title,
            detail=exc.detail,
            instance=request.url.path,
            **exc.extra,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Field-level errors are safe and useful; the raw input is not echoed.
        errors = [
            {"field": ".".join(str(p) for p in err["loc"][1:]), "message": err["msg"]}
            for err in exc.errors()
        ]
        return _problem(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            problem_type="validation-error",
            title="Request validation failed",
            detail="One or more fields are invalid.",
            instance=request.url.path,
            errors=errors,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return _problem(
            status_code=exc.status_code,
            problem_type="http-error",
            title="Request failed",
            detail=str(exc.detail),
            instance=request.url.path,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_exception", extra={"path": request.url.path})
        # Deliberately generic. The correlation ID is the bridge to the logs.
        return _problem(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            problem_type="internal-error",
            title="Internal server error",
            detail="An unexpected error occurred. Quote the request_id when reporting this.",
            instance=request.url.path,
        )
