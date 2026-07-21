"""Application configuration.

Settings are validated at import time. A misconfigured process must fail on
startup rather than surfacing as a 500 under load — a missing JWT secret is not
something to discover from a user report.

Every secret is typed `SecretStr`, so it cannot be printed, logged, or
serialised by accident: repr and str both render `**********`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------- app
    ENVIRONMENT: Environment = "local"
    PROJECT_NAME: str = "Vantage CRM API"
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "INFO"

    # Log format is deliberately independent of ENVIRONMENT: local development
    # wants readable lines, but you may still need to exercise the JSON path
    # locally when testing a log pipeline. `None` means "decide from
    # ENVIRONMENT" — see `use_json_logs`.
    LOG_JSON: bool | None = None

    @computed_field
    @property
    def use_json_logs(self) -> bool:
        if self.LOG_JSON is not None:
            return self.LOG_JSON
        return self.ENVIRONMENT != "local"

    @computed_field
    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    # -------------------------------------------------------- database
    POSTGRES_HOST: str = "127.0.0.1"  # see the REDIS_HOST note below
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "vantage"
    POSTGRES_USER: str = "vantage_app"
    POSTGRES_PASSWORD: SecretStr = SecretStr("")

    # Migrations run as a separate, more privileged role. The application role
    # must NOT own the tables and must NOT hold BYPASSRLS, or row-level security
    # is silently inert. See docs/DATABASE.md §2.
    POSTGRES_MIGRATION_USER: str = "vantage_migrator"
    POSTGRES_MIGRATION_PASSWORD: SecretStr = SecretStr("")

    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 5
    DB_ECHO: bool = False

    @computed_field
    @property
    def database_url(self) -> str:
        """Async DSN used by the application role."""
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:"
            f"{self.POSTGRES_PASSWORD.get_secret_value()}@"
            f"{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @computed_field
    @property
    def migration_database_url(self) -> str:
        """Sync DSN used by Alembic under the privileged migration role."""
        return (
            f"postgresql+psycopg://{self.POSTGRES_MIGRATION_USER}:"
            f"{self.POSTGRES_MIGRATION_PASSWORD.get_secret_value()}@"
            f"{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # ----------------------------------------------------------- redis
    # 127.0.0.1, not "localhost". On Windows and many Linux setups localhost
    # resolves to ::1 first; when the service listens on IPv4 only, every
    # connection stalls on the IPv6 attempt before falling back. Measured at
    # ~2s per attempt — enough to push concurrent refreshes past the grace
    # window and have legitimate clients flagged as token theft.
    REDIS_HOST: str = "127.0.0.1"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: SecretStr = SecretStr("")

    @computed_field
    @property
    def redis_url(self) -> str:
        auth = (
            f":{self.REDIS_PASSWORD.get_secret_value()}@"
            if self.REDIS_PASSWORD.get_secret_value()
            else ""
        )
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    # ------------------------------------------------------------ auth
    JWT_SECRET: SecretStr = SecretStr("")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_TTL_SECONDS: int = 15 * 60
    REFRESH_TOKEN_TTL_SECONDS: int = 30 * 24 * 60 * 60

    COOKIE_DOMAIN: str | None = None
    COOKIE_SECURE: bool = True
    ACCESS_COOKIE_NAME: str = "vg_access"
    REFRESH_COOKIE_NAME: str = "vg_refresh"
    CSRF_COOKIE_NAME: str = "vg_csrf"

    # Refresh-token rotation leeway.
    #
    # Strict rotation logs a legitimate user out whenever their client fires
    # two concurrent refreshes with the same token: the second presentation
    # looks exactly like a replay. Within this window a re-presented token is
    # treated as a client race rather than theft (risk R7).
    #
    # The trade-off is explicit: an attacker replaying a stolen token inside
    # this window gets a session. Seconds, not minutes — outside it, reuse
    # detection is unchanged.
    REFRESH_REUSE_GRACE_SECONDS: int = 10

    # Bounds how long one refresh blocks a concurrent one before giving up and
    # proceeding unserialised.
    REFRESH_LOCK_WAIT_MS: int = 3_000
    REFRESH_LOCK_TTL_MS: int = 5_000

    PASSWORD_MIN_LENGTH: int = 12
    LOGIN_MAX_ATTEMPTS: int = 5
    LOGIN_LOCKOUT_SECONDS: int = 15 * 60

    # --------------------------------------------------------- storage
    S3_ENDPOINT_URL: str | None = None  # set for MinIO; None for real AWS S3
    S3_REGION: str = "us-east-1"
    S3_BUCKET: str = "vantage-documents"
    S3_ACCESS_KEY_ID: SecretStr = SecretStr("")
    S3_SECRET_ACCESS_KEY: SecretStr = SecretStr("")
    S3_PRESIGN_TTL_SECONDS: int = 300

    # ------------------------------------------------------------ mail
    # Provider is swappable; business logic depends on NotificationService,
    # never on SES. See app/services/notifications/.
    EMAIL_PROVIDER: Literal["ses", "console"] = "console"
    EMAIL_FROM: str = "no-reply@vantagerealty.example"
    EMAIL_FROM_NAME: str = "Vantage CRM"
    AWS_SES_REGION: str = "us-east-1"
    AWS_SES_ACCESS_KEY_ID: SecretStr = SecretStr("")
    AWS_SES_SECRET_ACCESS_KEY: SecretStr = SecretStr("")
    AWS_SES_CONFIGURATION_SET: str | None = None

    # ------------------------------------------------------------- cors
    # Empty in production: the browser only ever talks to Next.js, which proxies
    # to this API over the internal network. See docs/ARCHITECTURE.md §2.
    CORS_ORIGINS: list[str] = Field(default_factory=list)

    # ------------------------------------------------------ validation
    @field_validator("LOG_JSON", "COOKIE_DOMAIN", "S3_ENDPOINT_URL", mode="before")
    @classmethod
    def _empty_string_means_unset(cls, value: object) -> object:
        """Treat `KEY=` in a .env file as "not set".

        dotenv yields an empty string for a bare key, which Pydantic then
        rejects for any non-str type. Optional settings must be commentable-out
        by blanking them, which is what people actually do.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("JWT_SECRET")
    @classmethod
    def _jwt_secret_is_strong(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if len(secret) < 32:
            raise ValueError(
                "JWT_SECRET must be at least 32 characters. "
                "Generate one with: openssl rand -hex 32"
            )
        return value

    @field_validator("LOG_LEVEL")
    @classmethod
    def _valid_log_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}")
        return upper

    def assert_production_ready(self) -> None:
        """Refuse to serve production traffic with development defaults.

        Called from the app lifespan. These are the settings that are harmless
        locally and dangerous in production, so they are checked explicitly
        rather than trusted to review.
        """
        if not self.is_production:
            return

        problems: list[str] = []
        if not self.COOKIE_SECURE:
            problems.append("COOKIE_SECURE must be true in production")
        if self.DB_ECHO:
            problems.append("DB_ECHO must be false in production (leaks SQL to logs)")
        if self.EMAIL_PROVIDER == "console":
            problems.append("EMAIL_PROVIDER must not be 'console' in production")
        if not self.POSTGRES_PASSWORD.get_secret_value():
            problems.append("POSTGRES_PASSWORD must be set")
        if self.CORS_ORIGINS:
            problems.append(
                "CORS_ORIGINS must be empty in production — the API is not "
                "browser-facing; Next.js proxies to it internally"
            )
        if problems:
            raise RuntimeError("Unsafe production configuration:\n  - " + "\n  - ".join(problems))


@lru_cache
def get_settings() -> Settings:
    """Cached accessor. Import this, never instantiate Settings directly."""
    return Settings()
