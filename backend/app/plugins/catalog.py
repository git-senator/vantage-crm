"""First-party plugin definitions — the proof the platform is the integration path.

These are the manifests the platform ships with, expressed as data rather than as
modules that edit the core. Each future integration (Slack, Stripe, Zapier, …) is
one entry here: a marketplace listing a tenant installs, subscribes, and
configures through the same lifecycle every third-party plugin uses. Synced into
the catalog as first-party (publisher-less, globally visible) plugins.

This module is data only; `PluginRegistryService.sync_first_party` validates each
manifest and upserts it, so a malformed first-party definition fails the same way
a bad third-party one does.
"""

from __future__ import annotations

FIRST_PARTY_PLUGINS: tuple[dict[str, object], ...] = (
    {
        "key": "slack",
        "name": "Slack",
        "version": "1.0.0",
        "description": "Post CRM events to a Slack channel.",
        "publisher": "Vantage",
        "category": "messaging",
        "capabilities": ["events.subscribe", "webhooks.manage"],
        "events": ["lead.created", "deal.created", "deal.updated"],
        "config_schema": [
            {"key": "channel", "label": "Channel", "type": "string", "required": True},
            {"key": "webhook_url", "label": "Incoming webhook URL",
             "type": "secret", "required": True},
        ],
    },
    {
        "key": "stripe",
        "name": "Stripe",
        "version": "1.0.0",
        "description": "Sync deals to Stripe as invoices.",
        "publisher": "Vantage",
        "category": "integration",
        "capabilities": ["records.read", "records.write", "events.subscribe"],
        "events": ["deal.created", "deal.updated"],
        "config_schema": [
            {"key": "api_key", "label": "Secret API key",
             "type": "secret", "required": True},
            {"key": "auto_invoice", "label": "Auto-create invoices",
             "type": "boolean", "required": False},
        ],
    },
    {
        "key": "zapier",
        "name": "Zapier",
        "version": "1.0.0",
        "description": "Trigger Zaps from CRM events.",
        "publisher": "Vantage",
        "category": "automation",
        "capabilities": ["events.subscribe", "webhooks.manage"],
        "events": sorted(
            ["lead.created", "lead.updated", "deal.created", "deal.updated",
             "property.created", "task.completed"]
        ),
        "config_schema": [
            {"key": "hook_url", "label": "Zap hook URL",
             "type": "secret", "required": True},
        ],
    },
)


__all__ = ["FIRST_PARTY_PLUGINS"]
