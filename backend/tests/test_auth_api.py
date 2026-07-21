"""Authentication HTTP layer.

Covers what the service tests cannot: cookie flags, response shape, and the
guarantee that no token is ever placed in a response body.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.cookies import REFRESH_COOKIE_PATH
from app.core.config import Settings
from app.models.user import User
from tests.conftest import VALID_PASSWORD

pytestmark = pytest.mark.integration


@pytest.fixture
def app(db: AsyncSession, settings: Settings):  # type: ignore[no-untyped-def]
    """App wired to the test session and settings.

    Both `get_db` and `get_tenant_session` are overridden to the test
    transaction so assertions can observe the same data the endpoint wrote.
    """
    from app.api.v1.dependencies import get_db, get_tenant_session
    from app.core.config import get_settings
    from app.main import create_app

    application = create_app()

    async def _session_override():  # type: ignore[no-untyped-def]
        yield db

    application.dependency_overrides[get_db] = _session_override
    application.dependency_overrides[get_tenant_session] = _session_override
    application.dependency_overrides[get_settings] = lambda: settings
    return application


@pytest.fixture
def client(app):  # type: ignore[no-untyped-def]
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url="http://test")


async def _login(client: AsyncClient, user: User):  # type: ignore[no-untyped-def]
    return await client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": VALID_PASSWORD},
    )


class TestLoginEndpoint:
    async def test_successful_login_returns_profile(
        self, client: AsyncClient, user: User
    ) -> None:
        async with client:
            response = await _login(client, user)

        assert response.status_code == 200
        body = response.json()
        assert body["user"]["email"] == user.email
        assert body["user"]["initials"] == "AC"
        assert body["user"]["organization"]["slug"] == user.organization.slug

    async def test_no_tokens_in_the_response_body(
        self, client: AsyncClient, user: User
    ) -> None:
        """A token readable by JavaScript is exfiltrable by any XSS."""
        async with client:
            response = await _login(client, user)

        raw = response.text
        assert "access_token" not in raw
        assert "refresh_token" not in raw

    async def test_password_hash_is_never_exposed(
        self, client: AsyncClient, user: User
    ) -> None:
        async with client:
            response = await _login(client, user)
        assert "password" not in response.text.lower()

    async def test_sets_all_three_cookies(
        self, client: AsyncClient, settings: Settings, user: User
    ) -> None:
        async with client:
            response = await _login(client, user)

        names = {c.split("=")[0] for c in response.headers.get_list("set-cookie")}
        assert settings.ACCESS_COOKIE_NAME in names
        assert settings.REFRESH_COOKIE_NAME in names
        assert settings.CSRF_COOKIE_NAME in names

    async def test_cookie_security_flags(
        self, client: AsyncClient, settings: Settings, user: User
    ) -> None:
        async with client:
            response = await _login(client, user)

        cookies = {
            header.split("=")[0]: header.lower()
            for header in response.headers.get_list("set-cookie")
        }

        access = cookies[settings.ACCESS_COOKIE_NAME]
        assert "httponly" in access
        assert "samesite=lax" in access

        refresh = cookies[settings.REFRESH_COOKIE_NAME]
        assert "httponly" in refresh
        # Strict: a refresh must never ride along on a cross-site request.
        assert "samesite=strict" in refresh
        # Scoped to the auth path so it is not attached to ordinary API calls.
        assert f"path={REFRESH_COOKIE_PATH}".lower() in refresh

        # CSRF cookie must be readable — double-submit requires it.
        assert "httponly" not in cookies[settings.CSRF_COOKIE_NAME]

    async def test_bad_credentials_return_401_problem_json(
        self, client: AsyncClient, user: User
    ) -> None:
        async with client:
            response = await client.post(
                "/api/v1/auth/login",
                json={"email": user.email, "password": "wrong-password"},
            )

        assert response.status_code == 401
        assert response.headers["content-type"].startswith("application/problem+json")
        body = response.json()
        assert body["type"].endswith("/authentication-failed")
        assert "request_id" in body

    async def test_unknown_and_wrong_password_are_indistinguishable(
        self, client: AsyncClient, user: User
    ) -> None:
        async with client:
            unknown = await client.post(
                "/api/v1/auth/login",
                json={"email": "nobody@example.com", "password": "whatever"},
            )
            wrong = await client.post(
                "/api/v1/auth/login",
                json={"email": user.email, "password": "wrong-password"},
            )

        assert unknown.status_code == wrong.status_code
        assert unknown.json()["detail"] == wrong.json()["detail"]

    async def test_malformed_email_is_rejected(self, client: AsyncClient) -> None:
        async with client:
            response = await client.post(
                "/api/v1/auth/login",
                json={"email": "not-an-email", "password": "x"},
            )

        assert response.status_code == 422
        assert response.json()["type"].endswith("/validation-error")


class TestProtectedEndpoints:
    async def test_me_requires_authentication(self, client: AsyncClient) -> None:
        async with client:
            response = await client.get("/api/v1/auth/me")
        assert response.status_code == 401

    async def test_me_returns_the_current_user(
        self, client: AsyncClient, user: User
    ) -> None:
        async with client:
            await _login(client, user)
            response = await client.get("/api/v1/auth/me")

        assert response.status_code == 200
        assert response.json()["email"] == user.email

    async def test_garbage_token_is_rejected(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        async with client:
            client.cookies.set(settings.ACCESS_COOKIE_NAME, "not-a-real-jwt")
            response = await client.get("/api/v1/auth/me")
        assert response.status_code == 401


class TestRefreshEndpoint:
    async def test_refresh_without_a_cookie_is_rejected(
        self, client: AsyncClient
    ) -> None:
        async with client:
            response = await client.post("/api/v1/auth/refresh")
        assert response.status_code == 401

    async def test_refresh_rotates_the_cookie(
        self, client: AsyncClient, settings: Settings, user: User
    ) -> None:
        async with client:
            await _login(client, user)
            original = client.cookies.get(settings.REFRESH_COOKIE_NAME)

            response = await client.post("/api/v1/auth/refresh")
            rotated = client.cookies.get(settings.REFRESH_COOKIE_NAME)

        assert response.status_code == 200
        assert rotated is not None
        assert rotated != original


class TestLogoutEndpoint:
    async def test_logout_clears_cookies(
        self, client: AsyncClient, user: User
    ) -> None:
        async with client:
            await _login(client, user)
            response = await client.post("/api/v1/auth/logout")

        assert response.status_code == 200
        # Expiry is signalled by Max-Age=0 on each cookie.
        cleared = [h.lower() for h in response.headers.get_list("set-cookie")]
        assert all("max-age=0" in h for h in cleared)

    async def test_logout_without_a_session_still_succeeds(
        self, client: AsyncClient
    ) -> None:
        async with client:
            response = await client.post("/api/v1/auth/logout")
        assert response.status_code == 200
