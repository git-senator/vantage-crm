"""Optional settings must tolerate a blank value in a .env file.

Regression guard: `LOG_JSON=` in .env crashed startup with a bool_parsing error,
because dotenv yields "" and Pydantic rejects that for `bool | None`. Blanking
a key is how people disable an optional setting, so it has to mean "unset".
"""

from __future__ import annotations

import pytest

from app.core.config import Settings

STRONG_SECRET = "a" * 32


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, JWT_SECRET=STRONG_SECRET, **overrides)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["LOG_JSON", "COOKIE_DOMAIN", "S3_ENDPOINT_URL"])
def test_blank_optional_value_is_treated_as_unset(field: str) -> None:
    assert getattr(_settings(**{field: ""}), field) is None


@pytest.mark.parametrize("field", ["LOG_JSON", "COOKIE_DOMAIN", "S3_ENDPOINT_URL"])
def test_whitespace_only_value_is_treated_as_unset(field: str) -> None:
    assert getattr(_settings(**{field: "   "}), field) is None


class TestLogFormatSelection:
    def test_local_defaults_to_readable_logs(self) -> None:
        assert _settings(ENVIRONMENT="local").use_json_logs is False

    def test_non_local_defaults_to_json(self) -> None:
        assert _settings(ENVIRONMENT="staging").use_json_logs is True

    def test_explicit_override_wins_locally(self) -> None:
        """Forcing JSON locally must work — it is how a log pipeline is tested."""
        assert _settings(ENVIRONMENT="local", LOG_JSON=True).use_json_logs is True

    def test_explicit_override_wins_in_production(self) -> None:
        assert _settings(ENVIRONMENT="staging", LOG_JSON=False).use_json_logs is False

    def test_blank_falls_back_to_environment(self) -> None:
        assert _settings(ENVIRONMENT="staging", LOG_JSON="").use_json_logs is True
