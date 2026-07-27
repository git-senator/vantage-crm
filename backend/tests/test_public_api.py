"""Public API (Phase 7.2).

The public surface is the internal services behind machine authentication. What
these tests pin down is the *contract* on top of that reuse:

  * a missing key is rejected as RFC 9457 problem+json;
  * the collection is cursor-paginated and sortable by creation time;
  * writes are attributed to the key's creator and gated by the creator's RBAC;
  * `Idempotency-Key` makes a retried write return the first response without
    creating a second record;
  * the API-key rate-limit bucket is keyed per credential, not per IP.

Authenticated requests run against a standalone public app whose machine
principal is overridden onto the test session — the same technique the auth
HTTP tests use — so assertions observe the rows the endpoints wrote.
"""

from __future__ import annotations

import dataclasses
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.public.app import create_public_app
from app.api.v1.dependencies import MachinePrincipal, get_machine_principal
from app.core.permissions import Scope
from app.models.organization import Organization
from app.services.rbac import AuthorizationContext
from tests.conftest import make_user

pytestmark = pytest.mark.integration


def _client(app):  # type: ignore[no-untyped-def]
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def machine_app(db: AsyncSession, admin):  # type: ignore[no-untyped-def]
    """The public app with the machine principal bound to the admin's context.

    The override yields a principal whose `api_key_id` is set (so it reads as a
    machine) and whose creator is the admin — exactly the shape
    `ApiKeyService.authenticate` produces, without needing a committed key row a
    separate transaction could see.
    """
    user, auth = admin
    machine_auth = dataclasses.replace(auth, api_key_id=uuid4())
    app = create_public_app()

    async def _override():  # type: ignore[no-untyped-def]
        yield MachinePrincipal(session=db, auth=machine_auth)

    app.dependency_overrides[get_machine_principal] = _override
    return app, user


async def _create_lead(client: AsyncClient, name: str, **headers: str):  # type: ignore[no-untyped-def]
    return await client.post(
        "/leads",
        json={"first_name": name, "last_name": "Buyer", "stage": "new"},
        headers=headers,
    )


# ----------------------------------------------------------- authentication


class TestAuthentication:
    async def test_missing_key_is_problem_json_401(self) -> None:
        """No override: the real machine chain runs and refuses an unkeyed call."""
        app = create_public_app()
        async with _client(app) as client:
            response = await client.get("/leads")

        assert response.status_code == 401
        assert response.headers["content-type"].startswith("application/problem+json")
        body = response.json()
        assert body["type"].endswith("/authentication-failed")
        assert body["status"] == 401


# --------------------------------------------------------------- read shape


class TestListing:
    async def test_list_is_cursor_paginated(self, machine_app) -> None:  # type: ignore[no-untyped-def]
        app, _user = machine_app
        async with _client(app) as client:
            await _create_lead(client, "Ada")
            await _create_lead(client, "Bo")
            await _create_lead(client, "Cy")

            first = await client.get("/leads", params={"limit": 2})
            body = first.json()
            assert first.status_code == 200
            assert len(body["data"]) == 2
            assert body["meta"]["has_more"] is True
            assert body["meta"]["next_cursor"]

            second = await client.get(
                "/leads", params={"limit": 2, "cursor": body["meta"]["next_cursor"]}
            )
            page2 = second.json()
            assert len(page2["data"]) == 1
            assert page2["meta"]["has_more"] is False

    async def test_sort_direction_flips_order(self, machine_app) -> None:  # type: ignore[no-untyped-def]
        app, _user = machine_app
        async with _client(app) as client:
            await _create_lead(client, "First")
            await _create_lead(client, "Second")

            newest = (await client.get("/leads", params={"sort": "-created_at"})).json()
            oldest = (await client.get("/leads", params={"sort": "created_at"})).json()

        assert [row["first_name"] for row in newest["data"]] == ["Second", "First"]
        assert [row["first_name"] for row in oldest["data"]] == ["First", "Second"]

    async def test_bad_sort_is_a_validation_problem(self, machine_app) -> None:  # type: ignore[no-untyped-def]
        app, _user = machine_app
        async with _client(app) as client:
            response = await client.get("/leads", params={"sort": "name"})
        assert response.status_code == 422
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_unknown_lead_is_problem_json_404(self, machine_app) -> None:  # type: ignore[no-untyped-def]
        app, _user = machine_app
        async with _client(app) as client:
            response = await client.get(f"/leads/{uuid4()}")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["type"].endswith("/not-found")


# --------------------------------------------------------------- write path


class TestWrites:
    async def test_create_attributes_to_the_key_creator(
        self, machine_app, db: AsyncSession
    ) -> None:  # type: ignore[no-untyped-def]
        app, user = machine_app
        async with _client(app) as client:
            response = await _create_lead(client, "Attributed")

        assert response.status_code == 201
        assert response.headers["location"].startswith("/api/public/v1/leads/")
        body = response.json()
        # Owner defaults to the actor, which is the key's creator.
        assert body["owner"]["id"] == str(user.id)

    async def test_write_requires_the_manage_permission(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        """A key holding only leads.view cannot create."""
        viewer = await make_user(db, organization, "viewer@vantage.example")
        view_only = AuthorizationContext(
            user_id=viewer.id,
            organization_id=organization.id,
            role_keys=("api_key",),
            grants={"leads.view": Scope.ALL},
            api_key_id=uuid4(),
        )
        app = create_public_app()

        async def _override():  # type: ignore[no-untyped-def]
            yield MachinePrincipal(session=db, auth=view_only)

        app.dependency_overrides[get_machine_principal] = _override
        async with _client(app) as client:
            response = await _create_lead(client, "Denied")

        assert response.status_code == 403
        assert response.json()["type"].endswith("/permission-denied")


# ------------------------------------------------------------- idempotency


class TestIdempotency:
    async def test_replayed_key_returns_first_response_without_re_creating(
        self, machine_app, db: AsyncSession
    ) -> None:  # type: ignore[no-untyped-def]
        app, _user = machine_app
        key_header = {"X-API-Key": "vk_test_credential", "Idempotency-Key": "abc-123"}
        async with _client(app) as client:
            first = await _create_lead(client, "Once", **key_header)
            assert first.status_code == 201
            created_id = first.json()["id"]

            replay = await _create_lead(client, "Once", **key_header)
            assert replay.status_code == 201
            assert replay.headers.get("idempotency-replayed") == "true"
            assert replay.json()["id"] == created_id

            # Exactly one lead exists, not two.
            listing = (await client.get("/leads")).json()
        assert len(listing["data"]) == 1

    async def test_key_is_scoped_so_a_distinct_key_still_executes(
        self, machine_app
    ) -> None:  # type: ignore[no-untyped-def]
        app, _user = machine_app
        cred = {"X-API-Key": "vk_test_credential"}
        async with _client(app) as client:
            await _create_lead(client, "A", **{**cred, "Idempotency-Key": "k1"})
            await _create_lead(client, "B", **{**cred, "Idempotency-Key": "k2"})
            listing = (await client.get("/leads")).json()
        assert len(listing["data"]) == 2


# --------------------------------------------------------------- rate limit


class TestRateLimitBucket:
    def test_api_key_credential_is_hashed_and_bucketed_per_key(self) -> None:
        from starlette.requests import Request

        from app.core import rate_limit
        from app.core.middleware import RateLimitMiddleware, _api_key_credential
        from app.core.security import create_api_key, hash_api_key

        raw = create_api_key().raw

        def _request(method: str) -> Request:
            scope = {
                "type": "http",
                "method": method,
                "path": "/api/public/v1/leads",
                "headers": [(b"x-api-key", raw.encode())],
                "client": ("203.0.113.9", 1234),
            }
            return Request(scope)

        assert _api_key_credential(_request("GET")) == hash_api_key(raw)

        identifier, limit = RateLimitMiddleware._bucket(_request("GET"))
        assert identifier == f"apikey:{hash_api_key(raw)}"
        assert limit is rate_limit.PUBLIC_API_GLOBAL

        _, mutation = RateLimitMiddleware._bucket(_request("POST"))
        assert mutation is rate_limit.PUBLIC_API_MUTATION
