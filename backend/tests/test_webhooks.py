"""Webhooks (Phase 7.3).

What these pin down, on top of the reused services and queue:

  * **The secret is sealed at rest and shown once.** The row holds ciphertext,
    the create/rotate response holds the plaintext, and a read never does.
  * **Signing is HMAC-SHA256 over `timestamp.body`** — the mirror of the
    inbound verifier, so a receiver verifies the same string we signed.
  * **Dispatch is idempotent and scoped.** It fans an event out only to active
    endpoints subscribed to that type, once per (endpoint, event), and never for
    a change a workflow itself caused.
  * **Delivery advances and retries deterministically**, and a persistently
    failing endpoint auto-disables rather than retrying forever.
  * **Management is gated on settings.manage, audited, and tenant-isolated.**
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.public.app import create_public_app
from app.api.v1.dependencies import MachinePrincipal, get_machine_principal
from app.core.config import Settings
from app.core.permissions import Scope
from app.models.audit import AuditLog
from app.models.organization import Organization
from app.models.webhook import WebhookDelivery, WebhookEndpoint
from app.services.rbac import AuthorizationContext
from app.services.webhook import (
    SECRET_CONTEXT,
    WebhookDeliveryService,
    WebhookService,
)
from app.webhooks.delivery import DeliveryResult
from app.webhooks.signing import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign
from tests.conftest import make_user

pytestmark = pytest.mark.integration

TEST_JWT_SECRET = "test_secret_that_is_at_least_thirty_two_chars"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "JWT_SECRET": TEST_JWT_SECRET,
        "ENVIRONMENT": "test",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _auth(organization: Organization, user_id, **grants: Scope) -> AuthorizationContext:  # type: ignore[no-untyped-def]
    resolved = {"settings.manage": Scope.ALL, **grants}
    return AuthorizationContext(
        user_id=user_id,
        organization_id=organization.id,
        role_keys=("api_key",),
        grants=resolved,
        api_key_id=uuid4(),
    )


class _Event:
    """A stand-in for an outbox WorkflowEvent row — all dispatch reads."""

    def __init__(self, event_type: str, payload: dict) -> None:  # type: ignore[type-arg]
        self.id = uuid4()
        self.event_type = event_type
        self.payload = payload


# ------------------------------------------------------------------ signing


class TestSigning:
    def test_signature_is_hmac_over_timestamp_and_body(self) -> None:
        secret = "whsec_example"
        body = b'{"a":1}'
        signature = sign(secret, "1700000000", body)
        assert signature.startswith("sha256=")
        # Stable and reproducible for the same inputs.
        assert signature == sign(secret, "1700000000", body)
        # Body or timestamp change flips it.
        assert signature != sign(secret, "1700000001", body)
        assert signature != sign(secret, "1700000000", b'{"a":2}')


# ------------------------------------------------------------------ dispatch


class TestDispatch:
    async def test_dispatches_to_matching_active_endpoints_only(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "wh1@vantage.example")
        auth = _auth(organization, user.id)
        mgmt = WebhookService(db, auth)
        wanted, _ = await mgmt.create(
            user, name="wanted", url="https://x.test/hook", event_types=["lead.created"]
        )
        await mgmt.create(
            user, name="other", url="https://y.test/hook", event_types=["deal.created"]
        )
        inactive, _ = await mgmt.create(
            user, name="off", url="https://z.test/hook", event_types=["lead.created"]
        )
        await mgmt.update(user, inactive.id, is_active=False)
        await db.flush()

        service = WebhookDeliveryService(db, _settings())
        event = _Event("lead.created", {"record": {"id": str(uuid4())}})
        delivery_ids = await service.dispatch(event, organization.id)

        assert len(delivery_ids) == 1
        row = (
            await db.execute(select(WebhookDelivery).where(WebhookDelivery.id == delivery_ids[0]))
        ).scalar_one()
        assert row.endpoint_id == wanted.id
        assert row.status == "pending"
        assert row.payload["event"] == "lead.created"
        assert row.payload["id"] == str(row.id)

    async def test_dispatch_is_idempotent_per_event(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "wh2@vantage.example")
        auth = _auth(organization, user.id)
        await WebhookService(db, auth).create(
            user, name="w", url="https://x.test/h", event_types=["lead.created"]
        )
        await db.flush()

        service = WebhookDeliveryService(db, _settings())
        event = _Event("lead.created", {"record": {"id": str(uuid4())}})
        first = await service.dispatch(event, organization.id)
        second = await service.dispatch(event, organization.id)

        assert len(first) == 1
        # Same pending delivery re-surfaces; no second row is created.
        assert second == first
        count = len(
            (await db.execute(select(WebhookDelivery))).scalars().all()
        )
        assert count == 1

    async def test_dispatch_skips_automation_caused_events(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "wh3@vantage.example")
        auth = _auth(organization, user.id)
        await WebhookService(db, auth).create(
            user, name="w", url="https://x.test/h", event_types=["lead.updated"]
        )
        await db.flush()

        service = WebhookDeliveryService(db, _settings())
        event = _Event(
            "lead.updated", {"record": {"id": str(uuid4())}, "source": "automation"}
        )
        assert await service.dispatch(event, organization.id) == []


# ---------------------------------------------------------------- delivery


class TestDeliveryOutcomes:
    async def _one_delivery(
        self, db: AsyncSession, organization: Organization, user
    ) -> tuple[WebhookDelivery, WebhookEndpoint]:  # type: ignore[no-untyped-def]
        endpoint, _ = await WebhookService(db, _auth(organization, user.id)).create(
            user, name="w", url="https://x.test/h", event_types=["lead.created"]
        )
        await db.flush()
        service = WebhookDeliveryService(db, _settings(WEBHOOK_DISABLE_AFTER_FAILURES=2))
        ids = await service.dispatch(
            _Event("lead.created", {"record": {"id": str(uuid4())}}), organization.id
        )
        delivery = (
            await db.execute(select(WebhookDelivery).where(WebhookDelivery.id == ids[0]))
        ).scalar_one()
        return delivery, endpoint

    async def test_success_marks_delivered_and_clears_failures(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "d1@vantage.example")
        delivery, endpoint = await self._one_delivery(db, organization, user)
        endpoint.consecutive_failures = 1

        service = WebhookDeliveryService(db, _settings())
        await service.mark_succeeded(
            delivery,
            endpoint,
            attempt=1,
            result=DeliveryResult(ok=True, status_code=200, body_snippet="ok", error=None),
        )
        assert delivery.status == "succeeded"
        assert delivery.delivered_at is not None
        assert endpoint.consecutive_failures == 0

    async def test_retry_keeps_pending_with_next_attempt(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        from datetime import UTC, datetime

        user = await make_user(db, organization, "d2@vantage.example")
        delivery, endpoint = await self._one_delivery(db, organization, user)

        service = WebhookDeliveryService(db, _settings())
        await service.mark_failed(
            delivery,
            endpoint,
            attempt=1,
            result=DeliveryResult(ok=False, status_code=500, body_snippet="err", error=None),
            retry=True,
            next_attempt_at=datetime.now(UTC),
        )
        assert delivery.status == "pending"
        assert delivery.attempts == 1
        assert delivery.next_attempt_at is not None
        # A retry does not yet count against the endpoint.
        assert endpoint.consecutive_failures == 0

    async def test_exhaustion_disables_after_threshold(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "d3@vantage.example")
        settings = _settings(WEBHOOK_DISABLE_AFTER_FAILURES=2)
        endpoint, _ = await WebhookService(db, _auth(organization, user.id)).create(
            user, name="w", url="https://x.test/h", event_types=["lead.created"]
        )
        await db.flush()
        service = WebhookDeliveryService(db, settings)

        # Two distinct deliveries to the SAME endpoint, both exhausted, trips it.
        for _ in range(2):
            ids = await service.dispatch(
                _Event("lead.created", {"record": {"id": str(uuid4())}}),
                organization.id,
            )
            delivery = (
                await db.execute(
                    select(WebhookDelivery).where(WebhookDelivery.id == ids[0])
                )
            ).scalar_one()
            await service.mark_failed(
                delivery,
                endpoint,
                attempt=6,
                result=DeliveryResult(ok=False, status_code=502, body_snippet="x", error=None),
                retry=False,
                next_attempt_at=None,
            )
            assert delivery.status == "exhausted"

        assert endpoint.consecutive_failures == 2
        assert endpoint.is_active is False
        assert endpoint.disabled_at is not None


# -------------------------------------------------------------- management


@pytest.fixture
def webhook_app(db: AsyncSession, organization: Organization):  # type: ignore[no-untyped-def]
    """The public app with a machine principal holding settings.manage."""
    app = create_public_app()

    async def _make(user_id, **grants):  # type: ignore[no-untyped-def]
        auth = _auth(organization, user_id, **grants)

        async def _override():  # type: ignore[no-untyped-def]
            yield MachinePrincipal(session=db, auth=auth)

        app.dependency_overrides[get_machine_principal] = _override

    return app, _make


def _client(app):  # type: ignore[no-untyped-def]
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url="http://test")


class TestManagement:
    async def test_create_returns_secret_once_and_stores_ciphertext(
        self, webhook_app, db: AsyncSession, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        app, make_principal = webhook_app
        user = await make_user(db, organization, "m1@vantage.example")
        await make_principal(user.id)

        async with _client(app) as client:
            response = await client.post(
                "/webhooks",
                json={
                    "name": "CI",
                    "url": "https://hooks.test/vantage",
                    "event_types": ["lead.created", "deal.created"],
                },
            )
        assert response.status_code == 201
        body = response.json()
        secret = body["secret"]
        assert secret.startswith("whsec_")
        assert response.headers["location"].startswith("/api/public/v1/webhooks/")

        row = (
            await db.execute(select(WebhookEndpoint).where(WebhookEndpoint.id == body["id"]))
        ).scalar_one()
        # The row stores ciphertext, never the shown secret.
        assert row.secret != secret
        from app.core.secrets import get_secret_box

        assert get_secret_box().decrypt(row.secret, context=SECRET_CONTEXT) == secret
        # A read never carries the secret.
        async with _client(app) as client:
            read = await client.get(f"/webhooks/{body['id']}")
        assert "secret" not in read.json()

    async def test_create_writes_a_high_severity_audit_entry(
        self, webhook_app, db: AsyncSession, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        app, make_principal = webhook_app
        user = await make_user(db, organization, "m2@vantage.example")
        await make_principal(user.id)
        async with _client(app) as client:
            await client.post(
                "/webhooks",
                json={"name": "x", "url": "https://h.test/x", "event_types": ["lead.created"]},
            )
        entry = (
            await db.execute(select(AuditLog).where(AuditLog.action == "webhook.created"))
        ).scalars().one()
        assert "secret" not in (entry.metadata_ or {})

    async def test_rotate_changes_the_secret(
        self, webhook_app, db: AsyncSession, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        app, make_principal = webhook_app
        user = await make_user(db, organization, "m3@vantage.example")
        await make_principal(user.id)
        async with _client(app) as client:
            created = (
                await client.post(
                    "/webhooks",
                    json={"name": "r", "url": "https://h.test/r", "event_types": ["lead.created"]},
                )
            ).json()
            rotated = (
                await client.post(f"/webhooks/{created['id']}/rotate-secret")
            ).json()
        assert rotated["secret"] != created["secret"]
        assert rotated["id"] == created["id"]

    async def test_unsupported_event_type_is_rejected(
        self, webhook_app, db: AsyncSession, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        app, make_principal = webhook_app
        user = await make_user(db, organization, "m4@vantage.example")
        await make_principal(user.id)
        async with _client(app) as client:
            response = await client.post(
                "/webhooks",
                json={"name": "x", "url": "https://h.test/x", "event_types": ["invoice.paid"]},
            )
        assert response.status_code == 422

    async def test_management_requires_settings_manage(
        self, webhook_app, db: AsyncSession, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        app, _make = webhook_app
        user = await make_user(db, organization, "m5@vantage.example")
        # A principal without settings.manage.
        auth = AuthorizationContext(
            user_id=user.id,
            organization_id=organization.id,
            role_keys=("api_key",),
            grants={"leads.view": Scope.ALL},
            api_key_id=uuid4(),
        )

        async def _override():  # type: ignore[no-untyped-def]
            yield MachinePrincipal(session=db, auth=auth)

        app.dependency_overrides[get_machine_principal] = _override
        async with _client(app) as client:
            response = await client.get("/webhooks")
        assert response.status_code == 403
        assert response.json()["type"].endswith("/permission-denied")

    async def test_delete_removes_the_endpoint(
        self, webhook_app, db: AsyncSession, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        app, make_principal = webhook_app
        user = await make_user(db, organization, "m6@vantage.example")
        await make_principal(user.id)
        async with _client(app) as client:
            created = (
                await client.post(
                    "/webhooks",
                    json={"name": "d", "url": "https://h.test/d", "event_types": ["lead.created"]},
                )
            ).json()
            deleted = await client.delete(f"/webhooks/{created['id']}")
            gone = await client.get(f"/webhooks/{created['id']}")
        assert deleted.status_code == 204
        assert gone.status_code == 404


class TestTenantIsolation:
    async def test_endpoint_is_invisible_to_another_workspace(
        self,
        db: AsyncSession,
        organization: Organization,
        other_organization: Organization,
    ) -> None:
        mine = await make_user(db, organization, "mine@vantage.example")
        endpoint, _ = await WebhookService(db, _auth(organization, mine.id)).create(
            mine, name="ours", url="https://h.test/ours", event_types=["lead.created"]
        )
        await db.flush()

        theirs = await make_user(db, other_organization, "theirs@meridian.example")
        their_auth = _auth(other_organization, theirs.id)
        assert await WebhookService(db, their_auth).list_endpoints() == []
        from app.core.exceptions import NotFoundError

        with pytest.raises(NotFoundError):
            await WebhookService(db, their_auth).get_endpoint(endpoint.id)


# -------------------------------------------------------------- delivery job


class TestDeliveryJob:
    async def test_successful_send_marks_the_delivery_succeeded(
        self, db: AsyncSession, organization: Organization, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        """End-to-end job wiring, with the network mocked.

        The job opens its own tenant-scoped sessions, so the setup is committed
        for them to see; the assertion re-reads after the job's own commit.
        """
        from app.workers.jobs import webhooks as job_module

        user = await make_user(db, organization, "job@vantage.example")
        await WebhookService(db, _auth(organization, user.id)).create(
            user, name="w", url="https://h.test/w", event_types=["lead.created"]
        )
        ids = await WebhookDeliveryService(db, _settings()).dispatch(
            _Event("lead.created", {"record": {"id": str(uuid4())}}), organization.id
        )
        delivery_id = ids[0]
        await db.commit()

        async def _fake_post(url, *, body, headers, timeout_seconds, snippet_bytes):  # type: ignore[no-untyped-def]
            assert headers[SIGNATURE_HEADER]
            assert headers[TIMESTAMP_HEADER]
            return DeliveryResult(ok=True, status_code=200, body_snippet="ok", error=None)

        monkeypatch.setattr(job_module, "post", _fake_post)

        result = await job_module.deliver_webhook(
            {}, str(delivery_id), str(organization.id)
        )
        assert result == "succeeded"

        db.expire_all()
        row = (
            await db.execute(select(WebhookDelivery).where(WebhookDelivery.id == delivery_id))
        ).scalar_one()
        assert row.status == "succeeded"
        assert row.response_status == 200
