"""In-process metrics: RED signals plus per-tenant, AI and webhook usage.

A deliberately small registry rather than a dependency. It holds labelled
counters and histograms, is safe to touch from any async task (a plain lock
around dict writes — the critical sections are microseconds), and renders the
Prometheus text format for a scrape. Metrics reset on restart, which is correct:
a counter is a rate source for a scraper that computes deltas, not a durable
ledger. The durable per-tenant record lives in `ai_jobs`, `audit_logs` and the
webhook tables, and the usage endpoint reads those.

**Cardinality is controlled at the call site.** The HTTP route label is the
*template* (`/leads/{lead_id}`), never the raw path, or every id would mint a
new series. Tenant appears on the request/error/usage counters — the per-tenant
question the requirements ask for — but not on the duration histogram, whose
bucket count would otherwise multiply by the tenant count.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

Labels = tuple[tuple[str, str], ...]

#: Seconds. Web-request shaped: sub-millisecond matters at the bottom, and the
#: long tail is capped where "slow" stops being interesting and starts being
#: "timed out".
_DURATION_BUCKETS: tuple[float, ...] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)


def _key(labels: dict[str, str]) -> Labels:
    return tuple(sorted((k, _clean(v)) for k, v in labels.items()))


def _clean(value: str) -> str:
    """Bound a label value so a hostile or accidental input cannot explode
    cardinality or break the exposition format."""
    text = str(value).replace("\\", "").replace('"', "").replace("\n", " ")
    return text[:120] if len(text) > 120 else text


@dataclass
class _Counter:
    help: str
    samples: dict[Labels, float] = field(default_factory=dict)


@dataclass
class _Histogram:
    help: str
    buckets: tuple[float, ...]
    # per label set: cumulative bucket counts, total count, running sum
    counts: dict[Labels, list[int]] = field(default_factory=dict)
    totals: dict[Labels, int] = field(default_factory=dict)
    sums: dict[Labels, float] = field(default_factory=dict)


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, _Counter] = {}
        self._histograms: dict[str, _Histogram] = {}

    def counter(self, name: str, help_text: str) -> None:
        with self._lock:
            self._counters.setdefault(name, _Counter(help=help_text))

    def histogram(
        self, name: str, help_text: str, buckets: tuple[float, ...] = _DURATION_BUCKETS
    ) -> None:
        with self._lock:
            self._histograms.setdefault(
                name, _Histogram(help=help_text, buckets=buckets)
            )

    def inc(self, name: str, labels: dict[str, str], value: float = 1.0) -> None:
        key = _key(labels)
        with self._lock:
            counter = self._counters.get(name)
            if counter is None:
                counter = self._counters.setdefault(name, _Counter(help=name))
            counter.samples[key] = counter.samples.get(key, 0.0) + value

    def observe(self, name: str, labels: dict[str, str], value: float) -> None:
        key = _key(labels)
        with self._lock:
            hist = self._histograms.get(name)
            if hist is None:
                hist = self._histograms.setdefault(
                    name, _Histogram(help=name, buckets=_DURATION_BUCKETS)
                )
            counts = hist.counts.get(key)
            if counts is None:
                counts = [0] * len(hist.buckets)
                hist.counts[key] = counts
                hist.totals[key] = 0
                hist.sums[key] = 0.0
            for i, edge in enumerate(hist.buckets):
                if value <= edge:
                    counts[i] += 1
            hist.totals[key] += 1
            hist.sums[key] += value

    def snapshot(self) -> dict[str, dict[Labels, float]]:
        """Counter values, for assertions and the usage view. Not the histograms."""
        with self._lock:
            return {
                name: dict(counter.samples)
                for name, counter in self._counters.items()
            }

    def render(self) -> str:
        """Prometheus text exposition (v0.0.4)."""
        lines: list[str] = []
        with self._lock:
            for name, counter in sorted(self._counters.items()):
                lines.append(f"# HELP {name} {counter.help}")
                lines.append(f"# TYPE {name} counter")
                for labels, value in sorted(counter.samples.items()):
                    lines.append(f"{name}{_fmt_labels(labels)} {_fmt_num(value)}")
            for name, hist in sorted(self._histograms.items()):
                lines.append(f"# HELP {name} {hist.help}")
                lines.append(f"# TYPE {name} histogram")
                for labels in sorted(hist.counts):
                    counts = hist.counts[labels]
                    cumulative = 0
                    for edge, count in zip(hist.buckets, counts, strict=True):
                        cumulative += count
                        le = _fmt_num(edge)
                        lines.append(
                            f"{name}_bucket{_fmt_labels(labels, ('le', le))} {cumulative}"
                        )
                    total = hist.totals[labels]
                    lines.append(
                        f"{name}_bucket{_fmt_labels(labels, ('le', '+Inf'))} {total}"
                    )
                    lines.append(f"{name}_sum{_fmt_labels(labels)} {_fmt_num(hist.sums[labels])}")
                    lines.append(f"{name}_count{_fmt_labels(labels)} {total}")
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        """Drop all series. For tests only."""
        with self._lock:
            self._counters.clear()
            self._histograms.clear()


def _fmt_labels(labels: Labels, extra: tuple[str, str] | None = None) -> str:
    pairs = list(labels)
    if extra is not None:
        pairs = [*pairs, extra]
    if not pairs:
        return ""
    inner = ",".join(f'{k}="{v}"' for k, v in pairs)
    return "{" + inner + "}"


def _fmt_num(value: float) -> str:
    if value == float("inf"):
        return "+Inf"
    if value == int(value):
        return str(int(value))
    return repr(value)


# --------------------------------------------------------------- the registry

REGISTRY = MetricsRegistry()

# RED — rate, errors, duration — for HTTP.
REGISTRY.counter("http_requests_total", "HTTP requests by method, route, status, tenant.")
REGISTRY.counter("http_errors_total", "HTTP responses with a 5xx status.")
REGISTRY.histogram("http_request_duration_seconds", "HTTP request duration.")
# RED for background jobs (webhook delivery included — it is a job).
REGISTRY.counter("job_runs_total", "Background job runs by name and outcome.")
REGISTRY.histogram("job_duration_seconds", "Background job duration.")
# AI usage and cost, sourced at the ledger write.
REGISTRY.counter("ai_completions_total", "AI completions by feature, model, status, tenant.")
REGISTRY.counter("ai_tokens_total", "AI tokens by feature and direction (prompt/completion).")
REGISTRY.counter("ai_cost_usd_total", "AI spend in USD by feature and tenant.")
# Webhook deliveries.
REGISTRY.counter("webhook_deliveries_total", "Webhook delivery attempts by outcome and tenant.")
# API usage attributed to API keys.
REGISTRY.counter("api_key_requests_total", "Requests authenticated by an API key, by tenant.")
# Integration syncs (Phase 7.7).
REGISTRY.counter(
    "integration_syncs_total",
    "Integration sync runs by provider, outcome and tenant.",
)
# GDPR / compliance data requests (Phase 8.0).
REGISTRY.counter(
    "data_requests_total",
    "GDPR data requests by kind, outcome and tenant.",
)
# Security operations (Phase 8.2).
REGISTRY.counter(
    "security_events_total",
    "Security events by type, severity and tenant.",
)
REGISTRY.counter(
    "security_alerts_total",
    "Security alerts raised by severity and tenant.",
)
# Operational resilience (Phase 8.6).
REGISTRY.counter(
    "operational_incidents_total",
    "Operational incidents by severity, action and tenant.",
)


_TENANT_UNKNOWN = "unknown"


def _tenant(organization_id: object | None) -> str:
    return str(organization_id) if organization_id else _TENANT_UNKNOWN


def status_class(status_code: int) -> str:
    """`2xx`, `4xx`, `5xx` — the axis a RED dashboard actually groups on."""
    return f"{status_code // 100}xx"


def record_http(
    *,
    method: str,
    route: str,
    status_code: int,
    duration_seconds: float,
    organization_id: object | None = None,
) -> None:
    cls = status_class(status_code)
    REGISTRY.inc(
        "http_requests_total",
        {"method": method, "route": route, "status": cls, "tenant": _tenant(organization_id)},
    )
    if status_code >= 500:
        REGISTRY.inc(
            "http_errors_total", {"method": method, "route": route, "status": cls}
        )
    REGISTRY.observe(
        "http_request_duration_seconds",
        {"method": method, "route": route, "status": cls},
        duration_seconds,
    )


def record_job(
    *, name: str, outcome: str, duration_seconds: float, organization_id: object | None = None
) -> None:
    REGISTRY.inc(
        "job_runs_total",
        {"job": name, "outcome": outcome, "tenant": _tenant(organization_id)},
    )
    REGISTRY.observe("job_duration_seconds", {"job": name}, duration_seconds)


def record_ai(
    *,
    feature: str,
    provider: str,
    model: str,
    status: str,
    organization_id: object | None = None,
    cost_usd: float = 0.0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> None:
    tenant = _tenant(organization_id)
    REGISTRY.inc(
        "ai_completions_total",
        {
            "feature": feature,
            "model": model,
            "provider": provider,
            "status": status,
            "tenant": tenant,
        },
    )
    if prompt_tokens:
        REGISTRY.inc(
            "ai_tokens_total",
            {"feature": feature, "direction": "prompt", "tenant": tenant},
            prompt_tokens,
        )
    if completion_tokens:
        REGISTRY.inc(
            "ai_tokens_total",
            {"feature": feature, "direction": "completion", "tenant": tenant},
            completion_tokens,
        )
    if cost_usd:
        REGISTRY.inc(
            "ai_cost_usd_total", {"feature": feature, "tenant": tenant}, cost_usd
        )


def record_webhook(
    *, outcome: str, organization_id: object | None = None
) -> None:
    REGISTRY.inc(
        "webhook_deliveries_total",
        {"outcome": outcome, "tenant": _tenant(organization_id)},
    )


def record_api_key_request(*, organization_id: object | None = None) -> None:
    REGISTRY.inc("api_key_requests_total", {"tenant": _tenant(organization_id)})


def record_integration_sync(
    *, provider: str, outcome: str, organization_id: object | None = None
) -> None:
    REGISTRY.inc(
        "integration_syncs_total",
        {"provider": provider, "outcome": outcome, "tenant": _tenant(organization_id)},
    )


def record_data_request(
    *, kind: str, outcome: str, organization_id: object | None = None
) -> None:
    REGISTRY.inc(
        "data_requests_total",
        {"kind": kind, "outcome": outcome, "tenant": _tenant(organization_id)},
    )


def record_security_event(
    *, event_type: str, severity: str, organization_id: object | None = None
) -> None:
    REGISTRY.inc(
        "security_events_total",
        {
            "event_type": event_type,
            "severity": severity,
            "tenant": _tenant(organization_id),
        },
    )


def record_security_alert(
    *, severity: str, organization_id: object | None = None
) -> None:
    REGISTRY.inc(
        "security_alerts_total",
        {"severity": severity, "tenant": _tenant(organization_id)},
    )


def record_operational_incident(
    *, severity: str, action: str, organization_id: object | None = None
) -> None:
    """Emit an operational-incident signal through the existing registry. This
    reuses the metrics infrastructure — it declares no new monitoring pipeline.
    `action` is "declared" or "resolved"."""
    REGISTRY.inc(
        "operational_incidents_total",
        {
            "severity": severity,
            "action": action,
            "tenant": _tenant(organization_id),
        },
    )
