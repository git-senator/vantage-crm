"""The plugin manifest specification — the contract a plugin declares itself by.

A manifest is what makes a plugin installable without a code change to the core:
it names the plugin, the capabilities it needs, the events it subscribes to, the
configuration it takes, and any feature flag or integration provider it depends
on. Validation is deterministic and total — every capability must be registered,
every event must be one the platform actually emits (the webhook vocabulary is
reused), and the version must be a semver — so a malformed manifest is rejected at
publish time rather than discovered at install time.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

from app.core.exceptions import AppError
from app.plugins.capabilities import validate_capabilities
from app.webhooks.events import WEBHOOK_EVENT_TYPES

#: A plugin's marketplace category.
PLUGIN_CATEGORIES: tuple[str, ...] = (
    "integration",
    "automation",
    "analytics",
    "messaging",
    "ai",
    "productivity",
    "other",
)

#: A configuration field's type. `secret` values are sealed at rest and never
#: read back in the clear.
CONFIG_FIELD_TYPES: tuple[str, ...] = ("string", "number", "boolean", "secret")

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,48}[a-z0-9]$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

#: The events a plugin may subscribe to — the same closed set webhooks use, so a
#: subscription can never wait on an event the platform never emits.
PLUGIN_EVENT_TYPES: frozenset[str] = WEBHOOK_EVENT_TYPES


class ManifestConfigField(BaseModel):
    key: str = Field(min_length=1, max_length=60)
    label: str = Field(min_length=1, max_length=120)
    type: str = "string"
    required: bool = False
    description: str | None = Field(default=None, max_length=500)

    @field_validator("type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        if value not in CONFIG_FIELD_TYPES:
            raise ValueError(f"Unknown config field type '{value}'.")
        return value


class PluginManifest(BaseModel):
    key: str = Field(min_length=3, max_length=50)
    name: str = Field(min_length=1, max_length=120)
    version: str
    description: str = Field(min_length=1, max_length=2000)
    publisher: str = Field(min_length=1, max_length=120)
    category: str = "other"
    capabilities: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    config_schema: list[ManifestConfigField] = Field(default_factory=list)
    #: An optional feature flag that must be enabled for the tenant to install.
    required_feature: str | None = Field(default=None, max_length=100)
    #: An optional link to an integration provider (Phase 7.7 registry), for a
    #: plugin that wraps a provider connection.
    provider_key: str | None = Field(default=None, max_length=60)

    @field_validator("key")
    @classmethod
    def _valid_key(cls, value: str) -> str:
        if not _KEY_RE.match(value):
            raise ValueError(
                "Plugin key must be lower_snake_case, 3-50 chars."
            )
        return value

    @field_validator("version")
    @classmethod
    def _valid_version(cls, value: str) -> str:
        if not _SEMVER_RE.match(value):
            raise ValueError("Plugin version must be a semver, e.g. 1.0.0.")
        return value

    @field_validator("category")
    @classmethod
    def _known_category(cls, value: str) -> str:
        if value not in PLUGIN_CATEGORIES:
            raise ValueError(f"Unknown plugin category '{value}'.")
        return value

    @field_validator("capabilities")
    @classmethod
    def _known_capabilities(cls, value: list[str]) -> list[str]:
        try:
            validate_capabilities(value)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc
        return value

    @field_validator("events")
    @classmethod
    def _known_events(cls, value: list[str]) -> list[str]:
        for event in value:
            if event not in PLUGIN_EVENT_TYPES:
                raise ValueError(f"Unknown or non-subscribable event '{event}'.")
        return value


def validate_manifest(data: dict[str, object]) -> PluginManifest:
    """Parse and validate a manifest, raising `AppError` on any problem.

    Wrapping Pydantic's `ValidationError` in `AppError` keeps the publish
    endpoint's failure a clean 400 with a readable message rather than a 500.
    """
    try:
        return PluginManifest.model_validate(data)
    except ValueError as exc:
        raise AppError(f"Invalid plugin manifest: {exc}") from exc


def secret_field_keys(manifest: PluginManifest) -> set[str]:
    """The config keys whose values must be sealed at rest."""
    return {f.key for f in manifest.config_schema if f.type == "secret"}


def required_config_keys(manifest: PluginManifest) -> set[str]:
    return {f.key for f in manifest.config_schema if f.required}


__all__ = [
    "CONFIG_FIELD_TYPES",
    "PLUGIN_CATEGORIES",
    "PLUGIN_EVENT_TYPES",
    "ManifestConfigField",
    "PluginManifest",
    "required_config_keys",
    "secret_field_keys",
    "validate_manifest",
]
