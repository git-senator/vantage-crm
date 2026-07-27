"""The shared Google OAuth 2.0 base for the Google providers.

Google Calendar and Google Contacts differ only in scopes and in what `sync`
fetches; the identity handshake — consent URL, code exchange, refresh, revoke —
is identical, so it lives here once. Both concrete providers subclass this and
supply their metadata and a `sync`.

Network calls use `httpx` and only happen when the provider is *configured* (a
client id and secret are present) and actually invoked. Nothing here runs at
import time, so a deployment without Google credentials carries no cost and the
provider simply reports itself unavailable.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import Settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.integrations.framework.types import OAuthTokens, ProviderMetadata

logger = get_logger(__name__)

_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105 — a URL
_REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"


class GoogleOAuthProvider:
    """OAuth mechanics common to every Google provider."""

    metadata: ProviderMetadata

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # ---------------------------------------------------------- identity

    def is_configured(self) -> bool:
        return bool(
            self._settings.GOOGLE_CLIENT_ID
            and self._settings.GOOGLE_CLIENT_SECRET.get_secret_value()
        )

    def authorize_url(
        self, *, redirect_uri: str, state: str, scopes: Sequence[str] | None = None
    ) -> str:
        from urllib.parse import urlencode

        chosen = list(scopes or self.metadata.default_scopes)
        params = {
            "client_id": self._settings.GOOGLE_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(chosen),
            "state": state,
            # offline + consent so Google returns a refresh token every time,
            # not only on the very first authorization.
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        }
        return f"{_AUTH_ENDPOINT}?{urlencode(params)}"

    async def exchange_code(self, *, code: str, redirect_uri: str) -> OAuthTokens:
        data = await self._token_request(
            {
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            }
        )
        tokens = self._tokens_from_response(data)
        account = await self._fetch_account(tokens.access_token)
        return OAuthTokens(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_at=tokens.expires_at,
            scopes=tokens.scopes or tuple(self.metadata.default_scopes),
            account_id=account.get("sub"),
            account_email=account.get("email"),
        )

    async def refresh_tokens(self, *, refresh_token: str) -> OAuthTokens:
        data = await self._token_request(
            {"refresh_token": refresh_token, "grant_type": "refresh_token"}
        )
        tokens = self._tokens_from_response(data)
        # Google omits the refresh token on a refresh; keep the existing one.
        return OAuthTokens(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token or refresh_token,
            expires_at=tokens.expires_at,
            scopes=tokens.scopes or tuple(self.metadata.default_scopes),
        )

    async def revoke(self, *, access_token: str, refresh_token: str | None) -> None:
        import httpx

        token = refresh_token or access_token
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(_REVOKE_ENDPOINT, data={"token": token})
        except Exception:  # pragma: no cover — best effort
            # Revocation is best-effort: the connection is torn down locally
            # regardless, and a token we cannot revoke expires on its own.
            logger.warning("google_token_revoke_failed", exc_info=True)

    # ------------------------------------------------------------ helpers

    async def _token_request(self, extra: dict[str, str]) -> dict[str, Any]:
        import httpx

        if not self.is_configured():
            raise AppError("Google integration is not configured.")
        payload = {
            "client_id": self._settings.GOOGLE_CLIENT_ID,
            "client_secret": self._settings.GOOGLE_CLIENT_SECRET.get_secret_value(),
            **extra,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(_TOKEN_ENDPOINT, data=payload)
        if response.status_code >= 400:
            raise AppError(
                f"Google token exchange failed ({response.status_code})."
            )
        result: dict[str, Any] = response.json()
        return result

    async def _fetch_account(self, access_token: str) -> dict[str, Any]:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    _USERINFO_ENDPOINT,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            if response.status_code >= 400:
                return {}
            data: dict[str, Any] = response.json()
            return data
        except Exception:  # pragma: no cover — identity is a nicety, not a gate
            logger.warning("google_userinfo_failed", exc_info=True)
            return {}

    def _tokens_from_response(self, data: dict[str, Any]) -> OAuthTokens:
        expires_in = data.get("expires_in")
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=int(expires_in))
            if expires_in
            else None
        )
        scope = data.get("scope", "")
        return OAuthTokens(
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token"),
            expires_at=expires_at,
            scopes=tuple(scope.split()) if scope else (),
        )

    async def _get_json(
        self, access_token: str, url: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Authenticated GET against a Google API, for a provider's `sync`."""
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                url,
                params={k: v for k, v in params.items() if v is not None},
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if response.status_code >= 400:
            raise AppError(f"Google API request failed ({response.status_code}).")
        result: dict[str, Any] = response.json()
        return result


__all__ = ["GoogleOAuthProvider"]
