"""App marketplace & plugin platform (Phase 9.0).

The extension platform that lets future integrations — Stripe, DocuSign, Slack,
Google Workspace, Microsoft 365, Zapier, WhatsApp and AI providers — ship as
*plugins* rather than as edits to the core. The deterministic core here is the
manifest specification, the capability registry (each capability mapped to the
RBAC permission it needs), and the install/enable/disable lifecycle state
machine.

Pure by design — a manifest in, a validated manifest or a precise error out; a
current state and an action in, the next state or a refusal out — so what a plugin
may do and how it moves through its lifecycle are reproducible and reviewable. The
catalog, tenant installations, and event subscriptions are the persisted surface
built on top; they reuse RBAC, feature flags, the webhook event vocabulary, and
audit logging rather than restating any of them.
"""

from __future__ import annotations
