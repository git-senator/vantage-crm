"""Correlation middleware behaviour.

Regression guard: the completion log originally fired after the contextvar was
reset in `finally`, so `request_completed` — the single most useful line for
tracing — was emitted without a request_id. Caught only by reading real
container output, which is why it is pinned here.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.logging import JsonFormatter, request_id_var
from app.core.middleware import REQUEST_ID_HEADER, CorrelationIdMiddleware


@pytest.fixture
def app() -> FastAPI:
    application = FastAPI()
    application.add_middleware(CorrelationIdMiddleware)

    @application.get("/ping")
    async def ping() -> dict[str, str]:
        return {"pong": "yes"}

    @application.get("/boom")
    async def boom() -> None:
        raise RuntimeError("intentional")

    return application


async def _get(app: FastAPI, path: str, **kwargs: object):  # type: ignore[no-untyped-def]
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, **kwargs)  # type: ignore[arg-type]


class TestCorrelationId:
    async def test_generates_id_when_absent(self, app: FastAPI) -> None:
        response = await _get(app, "/ping")
        assert response.headers[REQUEST_ID_HEADER]

    async def test_honours_inbound_id(self, app: FastAPI) -> None:
        """Lets a trace span the Next.js BFF and the API."""
        response = await _get(app, "/ping", headers={REQUEST_ID_HEADER: "trace-abc"})
        assert response.headers[REQUEST_ID_HEADER] == "trace-abc"

    async def test_caps_inbound_id_length(self, app: FastAPI) -> None:
        """The header is client-controlled; unbounded input must not reach logs."""
        response = await _get(app, "/ping", headers={REQUEST_ID_HEADER: "x" * 500})
        assert len(response.headers[REQUEST_ID_HEADER]) == 64

    async def test_context_is_reset_after_request(self, app: FastAPI) -> None:
        """A leaked contextvar would attribute later logs to the wrong request."""
        await _get(app, "/ping", headers={REQUEST_ID_HEADER: "trace-xyz"})
        assert request_id_var.get() is None


class _CapturingHandler(logging.Handler):
    """Formats each record at EMIT time, as a real handler does.

    This distinction is the whole point of these tests. `caplog` stores raw
    LogRecords and formats them afterwards — by which time the contextvar has
    been reset, so it cannot tell a correct implementation from a broken one.
    Formatting on emit reproduces production behaviour exactly.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setFormatter(JsonFormatter())
        self.payloads: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.payloads.append(json.loads(self.format(record)))

    def find(self, message: str) -> dict | None:
        return next((p for p in self.payloads if p["message"] == message), None)


@pytest.fixture
def captured() -> Iterator[_CapturingHandler]:
    handler = _CapturingHandler()
    logger = logging.getLogger("app.core.middleware")
    logger.addHandler(handler)
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


class TestCompletionLogCarriesCorrelationId:
    async def test_request_completed_includes_request_id(
        self, app: FastAPI, captured: _CapturingHandler
    ) -> None:
        await _get(app, "/ping", headers={REQUEST_ID_HEADER: "trace-log-1"})

        payload = captured.find("request_completed")
        assert payload is not None, "expected a request_completed log record"
        assert payload.get("request_id") == "trace-log-1", (
            "request_completed lost its correlation ID — the contextvar was "
            "reset before the log was emitted."
        )
        assert payload["context"]["status_code"] == 200
        assert payload["context"]["path"] == "/ping"

    async def test_failure_log_includes_request_id(
        self, app: FastAPI, captured: _CapturingHandler
    ) -> None:
        await _get(app, "/boom", headers={REQUEST_ID_HEADER: "trace-err-1"})

        payload = captured.find("request_failed")
        assert payload is not None, "expected a request_failed log record"
        assert payload.get("request_id") == "trace-err-1"
        assert "exception" in payload

    async def test_health_checks_are_not_logged(
        self, app: FastAPI, captured: _CapturingHandler
    ) -> None:
        """Probes run every few seconds and would drown real traffic."""

        @app.get("/health")
        async def health() -> dict[str, str]:
            return {"status": "ok"}

        await _get(app, "/health")
        assert captured.find("request_completed") is None
