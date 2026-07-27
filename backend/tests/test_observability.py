"""Observability (Phase 7.4).

What these pin down:

  * the metrics registry counts and renders Prometheus text correctly;
  * a correlation id (plus tenant/actor) binds and *unbinds* around a unit of
    work, so a pooled task cannot leak one job's context into the next;
  * HTTP requests land on the RED counters under the route *template*, not the
    raw path, and the probes/scrape are exempt;
  * a background job binds its correlation id and records its RED outcome;
  * the health endpoints report version/uptime and per-component readiness, and
    `/metrics` serves the exposition (and honours the token gate);
  * per-tenant usage reads the durable record (ledger, keys, webhooks).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import org_id_var, request_id_var, user_id_var
from app.core.permissions import Scope
from app.models.organization import Organization
from app.observability import metrics
from app.observability.correlation import correlation_scope, new_id
from app.services.rbac import AuthorizationContext
from tests.conftest import make_user

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _reset_metrics():  # type: ignore[no-untyped-def]
    metrics.REGISTRY.reset()
    yield
    metrics.REGISTRY.reset()


def _auth(organization: Organization, user_id) -> AuthorizationContext:  # type: ignore[no-untyped-def]
    return AuthorizationContext(
        user_id=user_id,
        organization_id=organization.id,
        role_keys=("admin",),
        grants={"settings.manage": Scope.ALL},
    )


# ------------------------------------------------------------ registry (pure)


class TestMetricsRegistry:
    def test_counter_and_labels(self) -> None:
        metrics.record_http(
            method="GET", route="/leads/{id}", status_code=200,
            duration_seconds=0.02, organization_id="org-1",
        )
        metrics.record_http(
            method="GET", route="/leads/{id}", status_code=200,
            duration_seconds=0.03, organization_id="org-1",
        )
        metrics.record_http(
            method="GET", route="/leads/{id}", status_code=500,
            duration_seconds=0.05, organization_id="org-1",
        )
        snap = metrics.REGISTRY.snapshot()
        ok_key = (
            ("method", "GET"), ("route", "/leads/{id}"),
            ("status", "2xx"), ("tenant", "org-1"),
        )
        assert snap["http_requests_total"][ok_key] == 2
        # The 5xx bumped both the request counter and the error counter.
        err_key = (("method", "GET"), ("route", "/leads/{id}"), ("status", "5xx"))
        assert snap["http_errors_total"][err_key] == 1

    def test_render_is_prometheus_text(self) -> None:
        metrics.record_http(
            method="POST", route="/deals", status_code=201,
            duration_seconds=0.2, organization_id="org-9",
        )
        text = metrics.REGISTRY.render()
        assert "# TYPE http_requests_total counter" in text
        expected = (
            'http_requests_total{method="POST",route="/deals",'
            'status="2xx",tenant="org-9"} 1'
        )
        assert expected in text
        # Histogram exposition: buckets, +Inf, sum and count.
        assert "# TYPE http_request_duration_seconds histogram" in text
        assert 'http_request_duration_seconds_bucket' in text
        assert 'le="+Inf"' in text
        assert "http_request_duration_seconds_count" in text

    def test_status_class(self) -> None:
        assert metrics.status_class(204) == "2xx"
        assert metrics.status_class(404) == "4xx"
        assert metrics.status_class(503) == "5xx"


# ---------------------------------------------------------- correlation (pure)


class TestCorrelation:
    def test_scope_binds_and_unbinds(self) -> None:
        assert request_id_var.get() is None
        with correlation_scope("cid-1", organization_id="org-7", user_id="u-3") as cid:
            assert cid == "cid-1"
            assert request_id_var.get() == "cid-1"
            assert org_id_var.get() == "org-7"
            assert user_id_var.get() == "u-3"
        # Reset on exit — no leak into the next unit of work.
        assert request_id_var.get() is None
        assert org_id_var.get() is None
        assert user_id_var.get() is None

    def test_missing_id_is_generated(self) -> None:
        with correlation_scope() as cid:
            assert cid
            assert request_id_var.get() == cid
        assert new_id() != new_id()


# ------------------------------------------------------------ RED middleware


class TestRedMiddleware:
    async def test_request_is_recorded_under_the_route_template(self) -> None:
        from app.main import create_app

        app = create_app()
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # No auth: a 401, but the route is matched so the template is known.
            resp = await client.get("/api/v1/leads")
            await client.get("/health")  # exempt — must not be counted

        assert resp.status_code == 401
        snap = metrics.REGISTRY.snapshot()
        requests = snap.get("http_requests_total", {})
        routes = {dict(k).get("route") for k in requests}
        assert "/api/v1/leads" in routes
        assert "/health" not in routes


# --------------------------------------------------------- job instrumentation


class TestJobInstrumentation:
    async def test_job_binds_correlation_and_records_outcome(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        from app.workers.runner import job

        seen: dict[str, str | None] = {}

        @job(organization_arg=0)
        async def _demo(ctx, organization_id):  # type: ignore[no-untyped-def]
            seen["rid"] = request_id_var.get()
            seen["org"] = org_id_var.get()
            return "ok"

        result = await _demo({"job_id": "job-abc"}, str(organization.id))

        assert result == "ok"
        # The job's id is its correlation id, and the tenant was bound.
        assert seen["rid"] == "job-abc"
        assert seen["org"] == str(organization.id)
        snap = metrics.REGISTRY.snapshot()
        key = (("job", "_demo"), ("outcome", "succeeded"), ("tenant", str(organization.id)))
        assert snap["job_runs_total"][key] == 1
        # Context did not leak out of the job.
        assert request_id_var.get() is None


# ------------------------------------------------------------ health & metrics


class TestHealthAndMetrics:
    async def _client(self):  # type: ignore[no-untyped-def]
        from app.main import create_app

        transport = ASGITransport(app=create_app(), raise_app_exceptions=False)
        return AsyncClient(transport=transport, base_url="http://test")

    async def test_liveness_reports_version_and_uptime(self) -> None:
        async with await self._client() as client:
            body = (await client.get("/health")).json()
            live = await client.get("/health/live")
        assert body["version"]
        assert body["uptime_seconds"] >= 0
        assert live.status_code == 200

    async def test_readiness_reports_component_detail(self) -> None:
        async with await self._client() as client:
            resp = await client.get("/health/ready")
        body = resp.json()
        assert set(body["checks"]) == {"database", "redis", "storage"}
        assert "latency_ms" in body["checks"]["database"]

    async def test_metrics_serves_exposition(self) -> None:
        metrics.record_http(
            method="GET", route="/x", status_code=200,
            duration_seconds=0.01, organization_id="org-1",
        )
        async with await self._client() as client:
            resp = await client.get("/metrics")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        assert "http_requests_total" in resp.text

    async def test_metrics_token_gate(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from app.api.v1 import health
        from app.core.config import Settings

        gated = Settings(
            _env_file=None,
            JWT_SECRET="test_secret_that_is_at_least_thirty_two_chars",
            ENVIRONMENT="test",
            METRICS_TOKEN="scrape-secret",  # type: ignore[arg-type]
        )
        monkeypatch.setattr(health, "get_settings", lambda: gated)
        async with await self._client() as client:
            unauth = await client.get("/metrics")
            authed = await client.get(
                "/metrics", headers={"Authorization": "Bearer scrape-secret"}
            )
        assert unauth.status_code == 401
        assert authed.status_code == 200


# ---------------------------------------------------------------- usage view


class TestUsage:
    async def test_tenant_usage_reads_the_durable_record(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        from app.core.security import create_api_key
        from app.models.ai import AiJob
        from app.models.api_key import ApiKey
        from app.services.observability import UsageService
        from app.services.webhook import WebhookService

        user = await make_user(db, organization, "usage@vantage.example")
        auth = _auth(organization, user.id)

        # An API key.
        gen = create_api_key()
        db.add(
            ApiKey(
                organization_id=organization.id,
                created_by=user.id,
                name="k",
                token_hash=gen.token_hash,
                prefix=gen.prefix,
                last_four=gen.last_four,
                scopes={},
                expires_at=datetime.now(UTC) + timedelta(days=30),
            )
        )
        # An AI ledger row this month.
        db.add(
            AiJob(
                organization_id=organization.id,
                feature="assistant",
                provider="echo",
                model="m",
                status="succeeded",
                prompt_tokens=10,
                completion_tokens=5,
                cost_usd=Decimal("0.002000"),
            )
        )
        # A webhook endpoint.
        await WebhookService(db, auth).create(
            user, name="w", url="https://h.test/w", event_types=["lead.created"]
        )
        await db.flush()

        usage = await UsageService(db, auth).tenant_usage()

        assert usage["api_keys"]["total"] == 1
        assert usage["api_keys"]["active"] == 1
        assert usage["webhooks"]["endpoints_total"] == 1
        assert usage["ai"]["calls"] == 1
        assert usage["ai"]["cost_usd"] == Decimal("0.002000")

    async def test_tenant_usage_requires_settings_manage(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        from app.core.exceptions import PermissionDeniedError
        from app.services.observability import UsageService

        user = await make_user(db, organization, "noperm@vantage.example")
        auth = AuthorizationContext(
            user_id=user.id,
            organization_id=organization.id,
            role_keys=("agent",),
            grants={"leads.view": Scope.OWN},
        )
        with pytest.raises(PermissionDeniedError):
            await UsageService(db, auth).tenant_usage()
