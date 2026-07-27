"""The public API's OpenAPI document, as the source of truth for the platform.

The public API (Phase 7.2) already describes itself: its sub-application produces
a self-contained OpenAPI document at `/api/public/v1/openapi.json`. The developer
portal, the API explorer, and the SDK generator all read *that* document rather
than a hand-kept second copy — so a new endpoint on the public API shows up in
the portal and the generated SDKs the moment it exists, with no parallel list to
forget to update.

The document is built once and cached: constructing the sub-app and resolving
its schema is not free, and the public contract does not change within a process
lifetime.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any


@lru_cache(maxsize=1)
def public_openapi_spec() -> dict[str, Any]:
    """The public API's OpenAPI 3.1 document.

    Built from the public sub-application so it is exactly the contract a caller
    hits — not a description that can drift from it. Cached for the process.
    """
    # Imported lazily so building the spec does not drag the public app (and its
    # router tree) into every module that imports the developer package.
    from app.api.public.app import create_public_app

    spec = create_public_app().openapi()
    # `openapi()` memoises onto the app instance, which we then discard; copy so
    # a caller cannot mutate the cached document.
    return dict(spec)


@lru_cache(maxsize=1)
def public_api_version() -> str:
    """The version string the public API advertises (e.g. `1.0.0`).

    The generated SDKs are stamped with this, so an SDK's version tracks the API
    contract it was generated from — that is what makes regeneration a versioned
    act rather than an untracked one.
    """
    info = public_openapi_spec().get("info", {})
    return str(info.get("version", "0.0.0"))


__all__ = ["public_api_version", "public_openapi_spec"]
