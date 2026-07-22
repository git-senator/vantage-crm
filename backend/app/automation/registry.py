"""The shared shape of a registry entry.

Triggers, conditions and actions are three registries with the same problem:
the backend has to validate a definition against them, and the frontend has to
draw a palette from them. Two hand-maintained copies of "what fields does the
send-email action take" drift within a week, so the registry is the single
source and the API serves it.

Config schemas are declared as a small list of `FieldSpec` rather than raw JSON
Schema. JSON Schema would be more expressive and much worse here: the builder
needs to render a form, and it would have to reimplement a schema interpreter to
do it. This describes exactly the widgets the builder has, and validation is a
few lines rather than a dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

#: Widget kinds the builder knows how to render. Adding one means adding a
#: control in the frontend, so the list is deliberately short.
FieldKind = Literal[
    "text",
    "textarea",
    "number",
    "boolean",
    "select",
    "entity_field",
    "user",
    "duration",
    "template",
]


@dataclass(frozen=True, slots=True)
class FieldOption:
    value: str
    label: str


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One configurable input on a node."""

    key: str
    label: str
    kind: FieldKind
    required: bool = True
    help_text: str | None = None
    options: tuple[FieldOption, ...] = ()
    #: Only meaningful for `select`; ignored otherwise.
    default: Any = None

    def validate(self, value: Any) -> str | None:
        """Return an error message, or None. Deliberately shallow.

        Type coercion is not attempted: a builder that sends a string where a
        number belongs has a bug, and silently coercing it hides the bug until
        a customer gets a workflow that does the wrong thing.
        """
        if value is None or (isinstance(value, str) and not value.strip()):
            return f"{self.label} is required." if self.required else None

        match self.kind:
            case "number":
                if not isinstance(value, int | float) or isinstance(value, bool):
                    return f"{self.label} must be a number."
            case "boolean":
                if not isinstance(value, bool):
                    return f"{self.label} must be true or false."
            case "select":
                allowed = {option.value for option in self.options}
                if allowed and str(value) not in allowed:
                    return f"{self.label} must be one of: {', '.join(sorted(allowed))}."
            case "duration":
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    return f"{self.label} must be a whole number of minutes."
            case _:
                if not isinstance(value, str):
                    return f"{self.label} must be text."
        return None


@dataclass(frozen=True, slots=True)
class Definition:
    """Common metadata for a registry entry."""

    key: str
    label: str
    description: str
    fields: tuple[FieldSpec, ...] = ()
    #: Free-form grouping for the builder's palette.
    category: str = "general"

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """Every problem with this node's configuration, not just the first.

        Returning all of them means the builder can highlight every bad field
        at once instead of making the user fix one, save, and discover another.
        """
        errors: list[str] = []
        known = {spec.key for spec in self.fields}

        for spec in self.fields:
            error = spec.validate(config.get(spec.key))
            if error:
                errors.append(error)

        # An unknown key is a real error, not noise: it means the builder and
        # the registry disagree, and silently dropping it would make a node do
        # less than its author configured.
        for key in config:
            if key not in known:
                errors.append(f"Unknown setting '{key}' for {self.label}.")

        return errors


@dataclass(frozen=True, slots=True)
class Registry[T: Definition]:
    """An immutable lookup with a stable ordering for the palette."""

    entries: dict[str, T] = field(default_factory=dict)

    def get(self, key: str) -> T | None:
        return self.entries.get(key)

    def require(self, key: str) -> T:
        entry = self.entries.get(key)
        if entry is None:
            raise KeyError(key)
        return entry

    def all(self) -> list[T]:
        return sorted(self.entries.values(), key=lambda item: (item.category, item.key))


def build_registry[T: Definition](*definitions: T) -> Registry[T]:
    """Assemble a registry, rejecting duplicate keys at import time.

    A duplicated key would silently shadow one entry with another, and the
    symptom — a node that validates but does the wrong thing — is a long way
    from the cause.
    """
    entries: dict[str, T] = {}
    for definition in definitions:
        if definition.key in entries:
            raise ValueError(f"Duplicate registry key: {definition.key}")
        entries[definition.key] = definition
    return Registry(entries=entries)
