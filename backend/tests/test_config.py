"""Configuration validation.

These guard the fail-fast behaviour: a misconfigured process must refuse to
start rather than serve traffic in an unsafe state.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

STRONG_SECRET = "a" * 32


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"JWT_SECRET": STRONG_SECRET, **overrides}
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


class TestJwtSecret:
    def test_rejects_short_secret(self) -> None:
        with pytest.raises(ValidationError, match="at least 32 characters"):
            Settings(_env_file=None, JWT_SECRET="too-short")  # type: ignore[arg-type]

    def test_accepts_strong_secret(self) -> None:
        assert _settings().JWT_SECRET.get_secret_value() == STRONG_SECRET

    def test_secret_is_not_exposed_by_repr(self) -> None:
        """A SecretStr must never render its value into a log line."""
        settings = _settings()
        assert STRONG_SECRET not in repr(settings)
        assert STRONG_SECRET not in str(settings.JWT_SECRET)


class TestLogLevel:
    def test_rejects_unknown_level(self) -> None:
        with pytest.raises(ValidationError):
            _settings(LOG_LEVEL="CHATTY")

    def test_normalises_case(self) -> None:
        assert _settings(LOG_LEVEL="debug").LOG_LEVEL == "DEBUG"


class TestProductionGuard:
    """`assert_production_ready` blocks development defaults in production."""

    def test_local_environment_is_never_blocked(self) -> None:
        _settings(ENVIRONMENT="local", COOKIE_SECURE=False).assert_production_ready()

    def test_rejects_insecure_cookies(self) -> None:
        settings = _settings(
            ENVIRONMENT="production",
            COOKIE_SECURE=False,
            POSTGRES_PASSWORD="x",
            EMAIL_PROVIDER="ses",
        )
        with pytest.raises(RuntimeError, match="COOKIE_SECURE"):
            settings.assert_production_ready()

    def test_rejects_console_email_provider(self) -> None:
        settings = _settings(
            ENVIRONMENT="production", POSTGRES_PASSWORD="x", EMAIL_PROVIDER="console"
        )
        with pytest.raises(RuntimeError, match="EMAIL_PROVIDER"):
            settings.assert_production_ready()

    def test_rejects_sql_echo(self) -> None:
        settings = _settings(
            ENVIRONMENT="production",
            POSTGRES_PASSWORD="x",
            EMAIL_PROVIDER="ses",
            DB_ECHO=True,
        )
        with pytest.raises(RuntimeError, match="DB_ECHO"):
            settings.assert_production_ready()

    def test_rejects_cors_origins(self) -> None:
        """The API is not browser-facing; CORS in production means containment broke."""
        settings = _settings(
            ENVIRONMENT="production",
            POSTGRES_PASSWORD="x",
            EMAIL_PROVIDER="ses",
            CORS_ORIGINS=["https://example.com"],
        )
        with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
            settings.assert_production_ready()

    def test_accepts_valid_production_config(self) -> None:
        _settings(
            ENVIRONMENT="production",
            POSTGRES_PASSWORD="a-real-password",
            EMAIL_PROVIDER="ses",
            COOKIE_SECURE=True,
            DB_ECHO=False,
            # Phase 5.6: a production config is not valid without a managed key.
            # The local provider keeps the key in the process environment, so
            # anything that can read the environment can read it.
            ENCRYPTION_PROVIDER="aws_kms",
            KMS_KEY_ID="arn:aws:kms:us-east-1:000000000000:key/abc",
        ).assert_production_ready()

    def test_rejects_the_local_key_provider(self) -> None:
        """Correct cryptography, ordinary key management — and ordinary is not
        good enough for the column holding everyone's second factor."""
        settings = _settings(
            ENVIRONMENT="production",
            POSTGRES_PASSWORD="a-real-password",
            EMAIL_PROVIDER="ses",
            COOKIE_SECURE=True,
            DB_ECHO=False,
            ENCRYPTION_PROVIDER="local",
        )
        with pytest.raises(RuntimeError, match="ENCRYPTION_PROVIDER"):
            settings.assert_production_ready()

    def test_rejects_kms_without_a_key(self) -> None:
        """Reported as unconfigured rather than crashing at the first enrolment."""
        settings = _settings(
            ENVIRONMENT="production",
            POSTGRES_PASSWORD="a-real-password",
            EMAIL_PROVIDER="ses",
            COOKIE_SECURE=True,
            DB_ECHO=False,
            ENCRYPTION_PROVIDER="aws_kms",
            KMS_KEY_ID=None,
        )
        with pytest.raises(RuntimeError, match="Secret encryption is not configured"):
            settings.assert_production_ready()


class TestDsnConstruction:
    def test_app_and_migration_roles_are_distinct(self) -> None:
        """If these ever converge, RLS silently stops enforcing anything."""
        settings = _settings()
        assert settings.POSTGRES_USER != settings.POSTGRES_MIGRATION_USER
        assert settings.POSTGRES_USER in settings.database_url
        assert settings.POSTGRES_MIGRATION_USER in settings.migration_database_url

    def test_app_dsn_uses_async_driver(self) -> None:
        assert _settings().database_url.startswith("postgresql+asyncpg://")
