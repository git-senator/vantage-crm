"""Curated integration templates — the marketplace seed, as data.

Each entry is one integration expressed entirely as data: its marketplace
metadata (vendor, category, certification, how it authenticates) plus the plugin
manifest that provisions it. There is no provider-specific *behaviour* here — a
template is a listing and a manifest, not a code path. When the platform later
implements an integration for real, it does so as a Phase 7.7 provider the
template's ``provider_key`` points at, or as an installed plugin's runtime; the
template itself does not change.

This is the marketplace's equivalent of ``FIRST_PARTY_PLUGINS``: a single
reviewable list of what the marketplace offers. :meth:`IntegrationTemplate.manifest`
emits a plain plugin manifest that the platform's own ``validate_manifest``
checks at sync time, so a malformed template fails exactly like a malformed
third-party one — the capabilities must be registered, the events must be
subscribable, the key and version must be well-formed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.marketplace.connections import AuthSpec, auth_spec

#: The plugin manifest version every curated template is published at.
_TEMPLATE_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class IntegrationTemplate:
    """A curated marketplace listing plus the plugin manifest behind it."""

    key: str
    name: str
    vendor: str
    #: The rich marketplace category (see ``categories``).
    category: str
    summary: str
    description: str
    auth: AuthSpec
    #: The curated certification tier (see ``certification``).
    certification: str
    #: The coarse plugin-platform category the provisioned plugin declares.
    plugin_category: str
    capabilities: tuple[str, ...] = ()
    events: tuple[str, ...] = ()
    config_schema: tuple[dict[str, Any], ...] = ()
    required_feature: str | None = None
    docs_url: str | None = None

    def manifest(self) -> dict[str, Any]:
        """The Phase 9.0 plugin manifest that provisions this integration."""
        return {
            "key": self.key,
            "name": self.name,
            "version": _TEMPLATE_VERSION,
            "description": self.description,
            "publisher": self.vendor,
            "category": self.plugin_category,
            "capabilities": list(self.capabilities),
            "events": list(self.events),
            "config_schema": [dict(field_) for field_ in self.config_schema],
            "required_feature": self.required_feature,
            "provider_key": self.auth.provider_key,
        }

    @property
    def oauth_scopes(self) -> list[str]:
        return list(self.auth.scopes)


def _field(
    key: str, label: str, type_: str = "string", *, required: bool = False
) -> dict[str, Any]:
    return {"key": key, "label": label, "type": type_, "required": required}


INTEGRATION_TEMPLATES: tuple[IntegrationTemplate, ...] = (
    IntegrationTemplate(
        key="google_workspace",
        name="Google Workspace",
        vendor="Google",
        category="productivity",
        summary="Sync calendar, contacts and tasks with Google Workspace.",
        description=(
            "Connect a Google Workspace account to keep calendar events and "
            "contacts in step with the CRM."
        ),
        auth=auth_spec(
            "oauth2",
            scopes=(
                "https://www.googleapis.com/auth/calendar",
                "https://www.googleapis.com/auth/contacts",
            ),
        ),
        certification="certified",
        plugin_category="productivity",
        capabilities=("records.read", "records.write", "events.subscribe"),
        events=("lead.created", "task.completed"),
        docs_url="https://developers.google.com/workspace",
    ),
    IntegrationTemplate(
        key="microsoft_365",
        name="Microsoft 365",
        vendor="Microsoft",
        category="productivity",
        summary="Sync Outlook calendar and contacts with Microsoft 365.",
        description=(
            "Connect a Microsoft 365 tenant to sync Outlook calendar and "
            "contacts with the CRM."
        ),
        auth=auth_spec(
            "oauth2",
            scopes=("Calendars.ReadWrite", "Contacts.ReadWrite", "offline_access"),
        ),
        certification="certified",
        plugin_category="productivity",
        capabilities=("records.read", "records.write", "events.subscribe"),
        events=("lead.created", "task.completed"),
        docs_url="https://learn.microsoft.com/graph",
    ),
    IntegrationTemplate(
        key="slack",
        name="Slack",
        vendor="Slack",
        category="messaging",
        summary="Post CRM events to a Slack channel.",
        description="Send lead and deal notifications to a Slack channel.",
        auth=auth_spec("webhook", requires_secret=True),
        certification="certified",
        plugin_category="messaging",
        capabilities=("events.subscribe", "webhooks.manage"),
        events=("lead.created", "deal.created", "deal.updated"),
        config_schema=(
            _field("channel", "Channel", "string", required=True),
            _field("webhook_url", "Incoming webhook URL", "secret", required=True),
        ),
        docs_url="https://api.slack.com/messaging/webhooks",
    ),
    IntegrationTemplate(
        key="stripe",
        name="Stripe",
        vendor="Stripe",
        category="payments",
        summary="Sync deals to Stripe as invoices.",
        description="Create Stripe invoices from CRM deals and track payment status.",
        auth=auth_spec("api_key", requires_secret=True),
        certification="certified",
        plugin_category="integration",
        capabilities=("records.read", "records.write", "events.subscribe"),
        events=("deal.created", "deal.updated"),
        config_schema=(
            _field("api_key", "Secret API key", "secret", required=True),
            _field("auto_invoice", "Auto-create invoices", "boolean"),
        ),
        docs_url="https://stripe.com/docs/api",
    ),
    IntegrationTemplate(
        key="docusign",
        name="DocuSign",
        vendor="DocuSign",
        category="esignature",
        summary="Send deal documents for e-signature.",
        description="Send documents attached to a deal for signature and track status.",
        auth=auth_spec("oauth2", scopes=("signature", "impersonation")),
        certification="certified",
        plugin_category="integration",
        capabilities=("records.read", "events.subscribe"),
        events=("deal.created", "deal.updated"),
        docs_url="https://developers.docusign.com",
    ),
    IntegrationTemplate(
        key="twilio",
        name="Twilio",
        vendor="Twilio",
        category="telephony",
        summary="Send SMS to contacts via Twilio.",
        description="Send text messages to leads and contacts through a Twilio number.",
        auth=auth_spec("api_key", requires_secret=True),
        certification="certified",
        plugin_category="messaging",
        capabilities=("messaging.send", "events.subscribe"),
        events=("lead.created", "deal.updated"),
        config_schema=(
            _field("account_sid", "Account SID", "string", required=True),
            _field("auth_token", "Auth token", "secret", required=True),
            _field("from_number", "From number", "string", required=True),
        ),
        docs_url="https://www.twilio.com/docs",
    ),
    IntegrationTemplate(
        key="zapier",
        name="Zapier",
        vendor="Zapier",
        category="automation",
        summary="Trigger Zaps from CRM events.",
        description="Fire a Zapier hook whenever a subscribed CRM event happens.",
        auth=auth_spec("webhook", requires_secret=True),
        certification="verified",
        plugin_category="automation",
        capabilities=("events.subscribe", "webhooks.manage"),
        events=(
            "lead.created",
            "lead.updated",
            "deal.created",
            "deal.updated",
            "property.created",
            "task.completed",
        ),
        config_schema=(_field("hook_url", "Zap hook URL", "secret", required=True),),
        docs_url="https://platform.zapier.com",
    ),
    IntegrationTemplate(
        key="openai",
        name="OpenAI",
        vendor="OpenAI",
        category="ai",
        summary="Use OpenAI models for AI features.",
        description="Route the workspace's AI features through an OpenAI API key.",
        auth=auth_spec("api_key", requires_secret=True),
        certification="certified",
        plugin_category="ai",
        capabilities=("ai.invoke",),
        config_schema=(
            _field("api_key", "API key", "secret", required=True),
            _field("model", "Default model", "string"),
        ),
        docs_url="https://platform.openai.com/docs",
    ),
    IntegrationTemplate(
        key="anthropic",
        name="Anthropic",
        vendor="Anthropic",
        category="ai",
        summary="Use Claude models for AI features.",
        description="Route the workspace's AI features through an Anthropic API key.",
        auth=auth_spec("api_key", requires_secret=True),
        certification="certified",
        plugin_category="ai",
        capabilities=("ai.invoke",),
        config_schema=(
            _field("api_key", "API key", "secret", required=True),
            _field("model", "Default model", "string"),
        ),
        docs_url="https://docs.anthropic.com",
    ),
    IntegrationTemplate(
        key="whatsapp_cloud",
        name="WhatsApp Cloud API",
        vendor="Meta",
        category="messaging",
        summary="Send WhatsApp messages to contacts.",
        description="Message leads and contacts through the WhatsApp Cloud API.",
        auth=auth_spec("api_key", requires_secret=True),
        certification="verified",
        plugin_category="messaging",
        capabilities=("messaging.send", "events.subscribe"),
        events=("lead.created",),
        config_schema=(
            _field("phone_number_id", "Phone number ID", "string", required=True),
            _field("access_token", "Access token", "secret", required=True),
        ),
        docs_url="https://developers.facebook.com/docs/whatsapp/cloud-api",
    ),
)

#: Keyed for lookup. Keys are globally unique, matching the plugin key.
_BY_KEY: dict[str, IntegrationTemplate] = {t.key: t for t in INTEGRATION_TEMPLATES}


def integration_template(key: str) -> IntegrationTemplate:
    """Look up a template by key. Raises ``KeyError`` for an unknown one."""
    try:
        return _BY_KEY[key]
    except KeyError as exc:
        raise KeyError(f"Unknown integration template '{key}'.") from exc


def all_templates() -> tuple[IntegrationTemplate, ...]:
    return INTEGRATION_TEMPLATES


# A small guard so a duplicate key in the curated list is a load-time error, not
# a silent last-write-wins in the lookup.
if len(_BY_KEY) != len(INTEGRATION_TEMPLATES):  # pragma: no cover - authoring guard
    raise RuntimeError("Duplicate key in INTEGRATION_TEMPLATES.")


__all__ = [
    "INTEGRATION_TEMPLATES",
    "IntegrationTemplate",
    "all_templates",
    "integration_template",
]
