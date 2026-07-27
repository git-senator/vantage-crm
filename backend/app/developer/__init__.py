"""The developer platform (Phase 7.6).

Everything a third party needs to build against the public API (Phase 7.2),
assembled from the surfaces the earlier phases already expose rather than a
parallel implementation:

  * `spec` — the public API's own OpenAPI document, and the version it carries.
  * `sdk` — a deterministic generator that turns that document into versioned
    TypeScript and Python client packages.
  * `onboarding` — the getting-started sequence and copy-pasteable quickstarts.

The HTTP surface that ties these together for a signed-in user lives in
`app/api/v1/developer.py`, backed by `DeveloperService`, which reuses the
existing API-key, webhook, billing and usage services — it never bypasses them.
"""

from __future__ import annotations

from app.developer.spec import public_api_version, public_openapi_spec

__all__ = ["public_api_version", "public_openapi_spec"]
