"""Google Contacts provider (People API).

`sync` pulls the account's connections incrementally with a `syncToken`, exactly
as the calendar provider does with events. Same division of labour: it returns
counts and the resume token; mapping a Google contact onto a CRM client is the
framework's job, not the adapter's.
"""

from __future__ import annotations

from typing import Any

from app.integrations.framework.types import ProviderMetadata, SyncOutcome
from app.integrations.providers.google.base import GoogleOAuthProvider

_CONNECTIONS_ENDPOINT = "https://people.googleapis.com/v1/people/me/connections"


class GoogleContactsProvider(GoogleOAuthProvider):
    metadata = ProviderMetadata(
        key="google_contacts",
        name="Google Contacts",
        category="contacts",
        description="Sync client contact details with Google Contacts.",
        default_scopes=(
            "https://www.googleapis.com/auth/contacts.readonly",
            "openid",
            "email",
        ),
        event_types=("client.created", "client.updated"),
        docs_url="https://developers.google.com/people",
    )

    async def sync(
        self,
        *,
        access_token: str,
        cursor: str | None,
        event: dict[str, Any] | None,
    ) -> SyncOutcome:
        params: dict[str, Any] = {
            "personFields": "names,emailAddresses,phoneNumbers",
            "pageSize": 200,
            "requestSyncToken": "true",
        }
        if cursor:
            params["syncToken"] = cursor
        data = await self._get_json(access_token, _CONNECTIONS_ENDPOINT, params)
        connections = data.get("connections", [])
        return SyncOutcome(
            items_processed=len(connections),
            cursor=data.get("nextSyncToken") or cursor,
            detail={"kind": "contacts.connections", "incremental": bool(cursor)},
        )


__all__ = ["GoogleContactsProvider"]
