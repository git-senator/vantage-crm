"""Optional OpenTelemetry tracing.

The whole module is written to be safe when the OpenTelemetry packages are not
installed: every import is guarded, and `span()` degrades to a no-op context
manager. So `OTEL_ENABLED=true` on a host without the SDK is harmless, and the
default install carries no new hard dependency — tracing is something a
deployment opts into by installing the packages and pointing at a collector.

When it *is* enabled and available, `configure_telemetry` sets up a tracer
provider with an OTLP exporter and instruments the FastAPI app, so HTTP spans,
plus the manual spans the job/AI paths open, ship to a collector. Correlation
in the logs does not depend on any of this — that is `contextvars` and works
regardless — this adds distributed traces on top for a deployment that wants
them.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_enabled = False


def is_enabled() -> bool:
    return _enabled


def configure_telemetry(app: Any, settings: Settings) -> None:
    """Wire up tracing if it is turned on and the packages are present.

    Failure here is logged and swallowed: observability must never be the reason
    the application will not start.
    """
    global _enabled
    if not settings.OTEL_ENABLED:
        return
    try:
        from opentelemetry import trace  # type: ignore[import-not-found]
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore[import-not-found]
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import (  # type: ignore[import-not-found]
            FastAPIInstrumentor,
        )
        from opentelemetry.sdk.resources import Resource  # type: ignore[import-not-found]
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import-not-found]
        from opentelemetry.sdk.trace.export import (  # type: ignore[import-not-found]
            BatchSpanProcessor,
        )
    except ImportError:
        logger.warning(
            "otel_packages_missing",
            extra={"hint": "pip install opentelemetry-sdk opentelemetry-exporter-otlp "
                   "opentelemetry-instrumentation-fastapi"},
        )
        return

    try:
        resource = Resource.create({"service.name": settings.OTEL_SERVICE_NAME})
        provider = TracerProvider(resource=resource)
        exporter = (
            OTLPSpanExporter(endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT)
            if settings.OTEL_EXPORTER_OTLP_ENDPOINT
            else OTLPSpanExporter()
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
        _enabled = True
        logger.info("otel_enabled", extra={"service": settings.OTEL_SERVICE_NAME})
    except Exception:
        logger.exception("otel_setup_failed")


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """A span when tracing is on, a no-op otherwise.

    Used by the job and AI paths so their work shows up as a trace when a
    collector is wired, without those call sites caring whether it is.
    """
    if not _enabled:
        yield None
        return
    try:
        from opentelemetry import trace
    except ImportError:  # pragma: no cover - _enabled implies importable
        yield None
        return
    tracer = trace.get_tracer("vantage-crm")
    with tracer.start_as_current_span(name) as current:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(key, str(value))
        yield current
