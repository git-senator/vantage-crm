"""Vantage plugin SDK (Phase 9.1) — the developer platform contract.

This package is the surface a third-party developer builds a plugin against. It
is **self-contained on purpose**: it imports only the standard library and
Pydantic, never `app.models`, `app.services`, `app.db` or any other internal CRM
module. A plugin therefore depends on a small, stable, versioned contract rather
than on the shape of the core, and the SDK could be extracted to its own
distributable package unchanged.

What it provides:

  * a **version** and a **compatibility layer** (`version`),
  * **typed event contracts** for the events a plugin can receive (`events`),
  * the **capability** vocabulary a plugin requests (`capabilities`),
  * the **extension interfaces** a plugin implements and the **service-injection
    interfaces** it calls (`interfaces`),
  * the **plugin context** passed to a handler (`context`),
  * the **backend extension hooks** a runtime dispatches through (`hooks`),
  * a **validation framework** for a manifest (`validation`),
  * and a **diagnostics** computation for an installation (`diagnostics`).

The backend bridges these contracts to the live plugin platform in
`app.services.sdk`; the runtime itself (Phase 9.0) is untouched.
"""

from __future__ import annotations

from app.sdk.version import SDK_VERSION

__all__ = ["SDK_VERSION"]
