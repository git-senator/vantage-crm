"""The public, machine-facing API (Phase 7.2).

A separate surface from the internal `/api/v1` the Next.js BFF talks to. It is
versioned, authenticated only by API keys (Phase 7.1), and mounted as its own
sub-application so it carries a self-contained OpenAPI document — the stable
contract third-party integrations build against, independent of the internal
API's shape.
"""
