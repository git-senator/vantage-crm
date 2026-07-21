"""Shared API primitives: cursor pagination and list envelopes.

Keyset (cursor) pagination, not OFFSET. Offset degrades linearly on deep pages
because the database still scans everything it skips, and it is *incorrect*
under concurrent writes: a row inserted while the user pages will shift the
window and silently duplicate or skip records.

The cursor encodes the sort key of the last row seen — here `(created_at, id)`.
`id` breaks ties, without which rows sharing a timestamp are unstable and can
be returned twice or never.
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25


class Cursor(BaseModel):
    """Position in a keyset-paginated list."""

    created_at: datetime
    id: UUID

    def encode(self) -> str:
        """Opaque base64 token.

        Opaque by intent: a client that parses it will depend on the sort key,
        and changing the ordering later would then be a breaking change.
        """
        payload = json.dumps(
            {"c": self.created_at.isoformat(), "i": str(self.id)},
            separators=(",", ":"),
        )
        return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")

    @classmethod
    def decode(cls, token: str) -> Cursor | None:
        """Parse a cursor. Returns None if malformed.

        A bad cursor yields the first page rather than a 400: cursors end up in
        bookmarked URLs, and a stale one should degrade gracefully rather than
        show an error page.
        """
        try:
            padded = token + "=" * (-len(token) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded))
            return cls(created_at=datetime.fromisoformat(data["c"]), id=UUID(data["i"]))
        except (
            ValueError,
            KeyError,
            TypeError,
            binascii.Error,
            json.JSONDecodeError,
        ):
            return None


class PageMeta(BaseModel):
    next_cursor: str | None = None
    has_more: bool = False
    #: Page size actually applied, which may be lower than requested.
    limit: int = DEFAULT_PAGE_SIZE


class Page[T](BaseModel):
    """Envelope for a paginated collection.

    Deliberately no `total`. Counting a filtered set costs a full scan on every
    page request, and under RLS the count is per-tenant so it cannot be cached
    globally. Clients that need "about N results" get it from a separate,
    explicitly-requested endpoint later.
    """

    data: list[T]
    meta: PageMeta = Field(default_factory=PageMeta)


class SortOrder(BaseModel):
    field: str
    descending: bool = True
