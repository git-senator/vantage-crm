"""Shared query-parameter primitives for the public list endpoints.

Pagination is keyset (cursor), exactly as the internal API — the public
contract inherits its correctness under concurrent writes and its stability on
deep pages. Sorting is deliberately narrow: the collections are ordered by
`created_at`, the indexed keyset column, in either direction. Exposing arbitrary
sort fields would either scan (OFFSET-style) or need a per-field cursor, and
neither belongs in a contract meant to stay stable.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Query

#: Where the public sub-app is mounted. Used to build absolute `Location`
#: headers, since a handler inside a mounted sub-app does not know its mount
#: point.
PUBLIC_V1_PREFIX = "/api/public/v1"

#: `created_at` (oldest first) or `-created_at` (newest first, the default).
CREATED_SORT = Annotated[
    str,
    Query(
        pattern="^-?created_at$",
        description="Sort by creation time: `created_at` (ascending) or "
        "`-created_at` (descending, the default).",
    ),
]


def is_ascending(sort: str) -> bool:
    """Map a validated sort token onto the repositories' `ascending` flag."""
    return not sort.startswith("-")
