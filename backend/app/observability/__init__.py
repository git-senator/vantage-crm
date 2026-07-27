"""Observability (Phase 7.4): metrics, tracing, correlation.

Three concerns, kept separate:

  * `metrics` — an in-process registry that records RED signals (rate, errors,
    duration) plus per-tenant, AI and webhook usage, and renders them in the
    Prometheus text format for `/metrics`. Dependency-free and always on.
  * `telemetry` — optional OpenTelemetry tracing. Real spans exported over OTLP
    when it is enabled *and* the packages are installed; a no-op otherwise, so a
    deployment without a collector pays nothing and imports never fail.
  * `correlation` — one helper to bind a correlation id (and tenant/actor) into
    the logging context for a unit of work, so a background job, an AI call or a
    webhook delivery is as traceable in the logs as an HTTP request already is.
"""
