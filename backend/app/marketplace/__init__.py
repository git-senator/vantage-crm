"""Integration marketplace — the curation layer over the plugin platform.

Phase 9.2. The marketplace does not connect to anyone. It is the *discovery,
certification and provisioning* layer that turns the plugin platform (Phase 9.0)
into a marketplace of integrations: a curated registry of listings, a category
taxonomy richer than the plugin platform's coarse buckets, a certification
framework that ranks a listing's trust, an OAuth/auth abstraction that *describes*
how a listing authenticates, and a health rollup that folds the plugin
diagnostics (Phase 9.1) and — when a listing wraps a live provider — the Phase 7.7
connection's health into one signal.

Everything here is deterministic and dependency-free, in the same spirit as the
SDK: pure data and pure functions, so the vocabulary can be tested and reasoned
about without a database. The live wiring — provisioning a listing as a plugin,
installing it per tenant, reading its diagnostics — lives in the services, which
reuse the plugin platform rather than restating it. No provider-specific business
logic lives here: an integration is a manifest template plus marketplace metadata,
not a code path.
"""

from __future__ import annotations

#: The marketplace vocabulary version. Bumped when the template/category/
#: certification shape changes in a way a consumer would need to notice.
MARKETPLACE_VERSION = "1.0.0"

__all__ = ["MARKETPLACE_VERSION"]
