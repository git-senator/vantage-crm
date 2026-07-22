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

    # ------------------------------------------------------------- mfa
    #: Roles that oblige a user to enrol in TOTP. Enforcement is a nudge with
    #: teeth rather than a lockout — see `mfa_required_for` for why refusing
    #: the login outright is the wrong shape.
    MFA_REQUIRED_ROLES: list[str] = Field(
        default_factory=lambda: ["owner", "admin"]
    )

    PASSWORD_MIN_LENGTH: int = 12
    LOGIN_MAX_ATTEMPTS: int = 5
    LOGIN_LOCKOUT_SECONDS: int = 15 * 60

    # --------------------------------------------------------- storage
    # Provider is swappable; business logic depends on ObjectStorage, never on
    # S3. See app/services/storage/. `memory` exists for tests and for a laptop
    # with no MinIO running, and is refused in production.
    STORAGE_PROVIDER: Literal["s3", "memory"] = "s3"

    S3_ENDPOINT_URL: str | None = None  # set for MinIO; None for real AWS S3

    # The endpoint a *browser* must use, when it differs from the one this
    # process uses. In compose the API reaches MinIO at `http://minio:9000`,
    # which no browser can resolve — and because SigV4 signs the Host header, a
    # URL cannot simply be rewritten after signing. So presigning uses a
    # separate client bound to this endpoint. Unset for real S3, where the
    # public and internal endpoints are the same thing.
    S3_PUBLIC_ENDPOINT_URL: str | None = None

    S3_REGION: str = "us-east-1"
    S3_BUCKET: str = "vantage-documents"
    S3_ACCESS_KEY_ID: SecretStr = SecretStr("")
    S3_SECRET_ACCESS_KEY: SecretStr = SecretStr("")
    #: Server-side encryption applied to every object and required on every
    #: presigned PUT. `AES256` is S3-managed keys; set `aws:kms` with a bucket
    #: key policy for customer-managed. Blank disables it — acceptable only
    #: where the bucket already has default encryption configured.
    S3_SERVER_SIDE_ENCRYPTION: str | None = "AES256"

    # Upload URLs are short because they are a bearer credential for writing to
    # a tenant's prefix: anyone holding one can write that object until it
    # expires. Downloads are shorter still — the URL is the only thing standing
    # between a leaked link and the file.
    S3_PRESIGN_TTL_SECONDS: int = 300
    S3_DOWNLOAD_TTL_SECONDS: int = 120

    #: Hard ceiling, enforced at finalization from the size storage reports —
    #: never from a client-declared number. 25 MB covers contracts, floor plans
    #: and photo sets; larger media is a different product decision.
    MAX_UPLOAD_BYTES: int = 25 * 1024 * 1024

    #: How long a registered-but-never-uploaded attachment stays claimable.
    #: After this the row is abandoned and the sweeper job (Phase 3.2) reaps it.
    UPLOAD_WINDOW_SECONDS: int = 3600

    #: When enabled, a verified upload is held at `pending_upload` with
    #: `scan_status='pending'` until the scan job clears it — the file is never
    #: servable in the window where its safety is unknown. When disabled,
    #: finalization publishes immediately and records `scan_status='skipped'`,
    #: which is honest about the fact that nothing looked at it.
    MALWARE_SCAN_ENABLED: bool = False

    #: Which scanner backs that switch. `eicar` detects the EICAR test file and
    #: nothing else — it exists to prove the quarantine pipeline, not to protect
    #: anything, and production refuses to start with scanning enabled while it
    #: is the configured engine. See app/services/storage/scanning.py.
    MALWARE_SCANNER: Literal["eicar"] = "eicar"

    #: Largest object the scanner will read into memory. Beyond this the file
    #: is left unscanned and unpublished rather than the worker being asked to
    #: buffer an arbitrary amount of hostile input.
    MALWARE_SCAN_MAX_BYTES: int = 16 * 1024 * 1024

    # ---------------------------------------------------------- workers
    #: How many jobs one worker process runs concurrently. Jobs are I/O bound
    #: (database, storage, mail), so this is well above the core count.
    WORKER_MAX_JOBS: int = 10
    #: Seconds before a job is considered hung and cancelled. Longer than any
    #: legitimate job here — the checksum of a 25 MB object is seconds.
    WORKER_JOB_TIMEOUT: int = 300
    #: Attempts before a job is dead-lettered. ARQ backs off exponentially
    #: between them.
    WORKER_MAX_TRIES: int = 5
    #: How often the sweeps run, in minutes.
    WORKER_SWEEP_INTERVAL_MINUTES: int = 15

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

    # -------------------------------------------------------- messaging
    #: Shared secret for the inbound-mail webhook's HMAC. Unset means inbound
    #: mail is refused outright — a default-open check on an unconfigured
    #: secret would accept anything that posted.
    INBOUND_WEBHOOK_SECRET: SecretStr = SecretStr("")

    # --------------------------------------------------------- whatsapp
    #: Meta Cloud API. Unset means the channel refuses to send rather than
    #: failing at the provider — an agent should learn it is unavailable from
    #: the compose box, not from a message stuck in `queued`.
    WHATSAPP_API_BASE: str = "https://graph.facebook.com/v21.0"
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_ACCESS_TOKEN: SecretStr = SecretStr("")
    #: Meta echoes this back during webhook subscription setup. Not a
    #: signature — it authenticates nothing after the handshake.
    WHATSAPP_VERIFY_TOKEN: SecretStr = SecretStr("")
    #: The app secret Meta signs webhook bodies with (`X-Hub-Signature-256`).
    #: Unset means inbound WhatsApp is refused, for the same reason as mail.
    WHATSAPP_APP_SECRET: SecretStr = SecretStr("")
    #: Which workspace inbound WhatsApp belongs to. Meta has no idea we are
    #: multi-tenant and sends no tenant hint, so single-tenant resolves it from
    #: configuration. Multi-tenant activation turns this into a lookup on the
    #: business phone number Meta *does* send.
    WHATSAPP_ORGANIZATION_ID: str = ""

    # ------------------------------------------------------------- cors
    # Empty in production: the browser only ever talks to Next.js, which proxies
    # to this API over the internal network. See docs/ARCHITECTURE.md §2.
    CORS_ORIGINS: list[str] = Field(default_factory=list)

    # ------------------------------------------------------ validation
    @field_validator(
        "LOG_JSON",
        "COOKIE_DOMAIN",
        "S3_ENDPOINT_URL",
        "S3_PUBLIC_ENDPOINT_URL",
        "S3_SERVER_SIDE_ENCRYPTION",
        "AWS_SES_CONFIGURATION_SET",
        mode="before",
    )
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
        if self.STORAGE_PROVIDER != "s3":
            problems.append(
                "STORAGE_PROVIDER must be 's3' in production — 'memory' loses "
                "every uploaded file when the process restarts"
            )
        # Blank credentials are legitimate on EC2/ECS, where boto3 resolves an
        # instance role — but only when there is no custom endpoint, because a
        # custom endpoint means MinIO and MinIO has no IAM.
        elif self.S3_ENDPOINT_URL and not self.S3_ACCESS_KEY_ID.get_secret_value():
            problems.append(
                "S3_ACCESS_KEY_ID must be set when S3_ENDPOINT_URL is "
                "configured (a custom endpoint has no instance role)"
            )
        if self.MALWARE_SCAN_ENABLED and self.MALWARE_SCANNER == "eicar":
            problems.append(
                "MALWARE_SCAN_ENABLED is on with the 'eicar' scanner, which "
                "detects only the EICAR test file. Configure a real engine or "
                "turn scanning off — a scanner that catches nothing is worse "
                "than none, because it looks like protection"
            )
        if self.S3_PRESIGN_TTL_SECONDS > 3600:
            problems.append(
                "S3_PRESIGN_TTL_SECONDS must be <= 3600 — a presigned URL is a "
                "bearer credential and a long-lived one survives its user"
            )
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
