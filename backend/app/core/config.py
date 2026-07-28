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

    # Read replica (Phase 8.1). A read-only endpoint reporting queries can be
    # routed to, offloading the primary. Unset means "no replica" — reads fall
    # back to the primary, so a single-node deployment is unaffected. The replica
    # uses the same application role and credentials; only the host differs.
    POSTGRES_REPLICA_HOST: str | None = None
    POSTGRES_REPLICA_PORT: int = 5432

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
    def has_read_replica(self) -> bool:
        return bool(self.POSTGRES_REPLICA_HOST)

    @computed_field
    @property
    def read_database_url(self) -> str:
        """Async DSN for read-only work. The replica when configured, else the
        primary — so a caller can always ask for the read endpoint and get a
        correct connection regardless of whether a replica exists."""
        if not self.POSTGRES_REPLICA_HOST:
            return self.database_url
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:"
            f"{self.POSTGRES_PASSWORD.get_secret_value()}@"
            f"{self.POSTGRES_REPLICA_HOST}:{self.POSTGRES_REPLICA_PORT}/{self.POSTGRES_DB}"
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

    # The longest life an API key may be issued for (Phase 7.1). A key is a
    # long-lived credential, so an upper bound forces periodic rotation rather
    # than letting a leaked key stay valid forever.
    API_KEY_MAX_TTL_DAYS: int = 365

    # ---------------------------------------------------------- webhooks
    # Outbound webhooks (Phase 7.3). Delivery rides the ARQ queue and reuses the
    # `@job` exponential backoff, so these bound the delivery attempt itself.
    #: Attempts before a delivery is marked exhausted. Six attempts over the
    #: capped backoff span several minutes — long enough to ride out a brief
    #: consumer outage, short enough not to hammer a dead endpoint for hours.
    WEBHOOK_MAX_ATTEMPTS: int = 6
    #: Per-request timeout for a delivery POST. A slow consumer must not hold a
    #: worker slot open.
    WEBHOOK_TIMEOUT_SECONDS: float = 10.0
    #: Consecutive failed deliveries before an endpoint is auto-disabled. A
    #: permanently broken URL stops consuming the queue instead of retrying
    #: forever; the owner re-enables it after fixing the receiver.
    WEBHOOK_DISABLE_AFTER_FAILURES: int = 20
    #: How much of a non-2xx response body to keep for the delivery record.
    #: Enough to diagnose, bounded so a chatty error page cannot bloat the row.
    WEBHOOK_RESPONSE_SNIPPET_BYTES: int = 2000

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

    #: Which scanner backs that switch. `clamav` is the production answer,
    #: spoken to over clamd's INSTREAM protocol. `eicar` detects the EICAR test
    #: file and nothing else — it exists to prove the quarantine pipeline, not
    #: to protect anything, and production refuses to start with scanning
    #: enabled while it is the configured engine.
    #: See app/services/storage/scanning.py.
    MALWARE_SCANNER: Literal["eicar", "clamav"] = "eicar"

    CLAMAV_HOST: str = "clamav"
    CLAMAV_PORT: int = 3310
    #: Wraps the whole exchange. A scanner that hangs holds a worker slot
    #: indefinitely, and a queue of stalled scans is worse than a scan that
    #: gives up and is retried.
    CLAMAV_TIMEOUT_SECONDS: float = 30.0

    #: Largest object the scanner will read into memory. Beyond this the file
    #: is left unscanned and unpublished rather than the worker being asked to
    #: buffer an arbitrary amount of hostile input.
    MALWARE_SCAN_MAX_BYTES: int = 16 * 1024 * 1024

    # ------------------------------------------------- secrets at rest
    #: Where data keys come from. `local` reads them from configuration —
    #: correct cryptography, ordinary key management, honest for local and
    #: staging. `aws_kms` uses envelope encryption, so the plaintext data key
    #: never reaches disk and every unwrap is an IAM call CloudTrail records.
    #: See app/core/secrets.py.
    ENCRYPTION_PROVIDER: Literal["local", "aws_kms"] = "local"

    #: `id:base64-key,id:base64-key`. More than one so a rotation is a config
    #: change rather than a migration: the key id travels inside each token, so
    #: old values stay readable while new writes use the active key.
    ENCRYPTION_KEYS: SecretStr = SecretStr("")
    ENCRYPTION_ACTIVE_KEY_ID: str = "primary"

    #: The KMS key ARN or alias, when the provider is `aws_kms`.
    KMS_KEY_ID: str | None = None
    #: Falls back to S3_REGION, since a deployment almost always keeps its key
    #: and its bucket in one region and two settings that must agree are two
    #: settings that will eventually disagree.
    KMS_REGION: str | None = None

    @property
    def kms_region(self) -> str:
        return self.KMS_REGION or self.S3_REGION

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

    # -------------------------------------------------------------- ai
    # Provider is swappable; business logic depends on AIService, never on the
    # Anthropic API. See app/services/ai/ and docs/AI.md.
    #
    # `echo` is the default deliberately: it never calls a model and cannot leak
    # a customer's data to a third party during local development or a test run,
    # which is the most serious class of AI-integration accident. Production must
    # set a real provider *and* opt in — see assert_production_ready.
    AI_PROVIDER: Literal["anthropic", "echo"] = "echo"

    #: Master switch. Off by default: the AI layer sends CRM data to an external
    #: model, so it is opt-in per deployment rather than on the moment a key is
    #: present. Every AI entry point checks this before doing anything.
    AI_ENABLED: bool = False

    AI_API_BASE: str = "https://api.anthropic.com"
    AI_API_KEY: SecretStr = SecretStr("")
    #: The default model. Sonnet is the capable, cost-sensible default for
    #: assistant and analysis work; cheaper models are selected per feature
    #: (lead scoring runs on Haiku) rather than globally.
    AI_MODEL: str = "claude-sonnet-5"
    AI_TIMEOUT_SECONDS: float = 60.0

    #: Hard monthly cost ceiling per organization, in USD. Enforced *before*
    #: dispatch (SECURITY.md §5): once a tenant's month-to-date spend reaches
    #: this, further calls are refused rather than merely logged. 0 disables the
    #: ceiling, which production forbids.
    AI_MONTHLY_COST_CEILING_USD: float = 50.0

    #: Ceiling on tokens a single completion may generate. The per-call guard
    #: that stops one runaway request, distinct from the tenant-month ceiling.
    AI_MAX_OUTPUT_TOKENS: int = 1024

    # ---------------------------------------------------- billing
    # Phase 7.5. The provider is swappable; business logic depends on
    # BillingProvider, never on Stripe. `manual` is the default and calls no
    # external service — correct for self-hosted and for tests.
    BILLING_PROVIDER: Literal["manual", "stripe"] = "manual"
    #: When off (the default), quota and entitlement checks pass through — a
    #: tenant without a plan is never blocked, which preserves existing
    #: behaviour. Turn it on to enforce a subscription's limits.
    BILLING_ENFORCED: bool = False
    #: The plan a tenant falls back to with no active subscription, and after a
    #: grace period lapses. Must match a seeded plan key.
    BILLING_DEFAULT_PLAN: str = "free"
    #: Days a `past_due` subscription keeps its entitlements before it downgrades
    #: to the default plan — the dunning window, so one failed charge does not
    #: instantly lock a paying customer out.
    BILLING_GRACE_PERIOD_DAYS: int = 14

    STRIPE_API_KEY: SecretStr = SecretStr("")
    #: Verifies inbound Stripe webhook signatures. Unset means the webhook is
    #: refused, the same default-closed stance the inbound-mail webhook takes.
    STRIPE_WEBHOOK_SECRET: SecretStr = SecretStr("")
    #: Where the Stripe billing portal returns the user afterwards.
    STRIPE_PORTAL_RETURN_URL: str | None = None

    # ---------------------------------------------------- integrations
    # Phase 7.7. The integration platform is provider-agnostic; credentials are
    # per external service. A provider with no credentials is listed in the
    # catalogue but refuses to install — an honest "unavailable", not a silent
    # absence. `manual`/`mock` providers exist for local and tests only.
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: SecretStr = SecretStr("")
    #: Where Google returns the user after consent. Set per deployment; the
    #: install flow also accepts an explicit redirect so a caller can override it.
    GOOGLE_OAUTH_REDIRECT_URI: str | None = None

    #: How often the periodic sweep re-syncs an active connection.
    INTEGRATION_SYNC_INTERVAL_MINUTES: int = 60
    #: Consecutive failed syncs before a connection is auto-disabled (status →
    #: error) rather than retried forever. Its owner re-enables it after fixing
    #: the cause.
    INTEGRATION_DISABLE_AFTER_FAILURES: int = 10
    #: Registers the deterministic, no-network mock provider. For local and
    #: tests; production refuses it — a mock integration in production is a
    #: connection that pretends to sync.
    INTEGRATIONS_ENABLE_MOCK: bool = False

    # ----------------------------------------- compliance operations
    # Phase 8.3. The SLA, in days, within which a GDPR subject request must be
    # handled; a request older than this and still open is flagged overdue by
    # the DSAR workflow and fails the `gdpr.dsar_process` control. 30 days is the
    # GDPR default.
    COMPLIANCE_DSAR_SLA_DAYS: int = 30

    # ------------------------------------------------ trust & risk
    # Phase 8.4. A certified attestation within this many days of its expiry is
    # flagged "expiring soon" on the trust dashboard — the window to start a
    # renewal audit before the certification lapses. 60 days is a common lead
    # time for a SOC 2 renewal engagement.
    TRUST_CERT_EXPIRY_WARNING_DAYS: int = 60

    # ------------------------------------------------ data governance
    # Phase 8.5. An active data asset that has not been reviewed within this many
    # days is counted "unreviewed" on the governance dashboard — the signal that a
    # catalog entry's classification and ownership may have gone stale. 180 days
    # is a common semi-annual data-review cadence.
    GOVERNANCE_UNREVIEWED_ASSET_DAYS: int = 180

    # --------------------------------------- operational resilience
    # Phase 8.6. An active continuity plan not tested within this many days is
    # flagged overdue on the resilience dashboard and counts against operational
    # readiness. 180 days is a common semi-annual DR-test cadence.
    RESILIENCE_PLAN_REVIEW_INTERVAL_DAYS: int = 180
    #: The window, in days, over which a resolved incident's recovery breach is
    #: counted "recent" for the readiness rating.
    RESILIENCE_INCIDENT_LOOKBACK_DAYS: int = 90

    # --------------------------------------- app marketplace & plugins
    # Phase 9.0. The ceiling on how many plugins one tenant may install, a guard
    # against a runaway or abusive install loop. Generous by default; a tenant
    # that legitimately needs more is a support conversation, not a silent cap.
    PLUGINS_MAX_PER_TENANT: int = 100
    #: Phase 9.1. When true, the SDK validation endpoint treats warnings as
    #: failures — a stricter gate for a marketplace that requires clean manifests.
    SDK_STRICT_VALIDATION: bool = False

    # ------------------------------------------- security operations
    # Phase 8.2. Thresholds for the security-ops detection framework. Reused by
    # the deterministic detectors, so a deployment can tune sensitivity without a
    # code change.
    #: Failed sign-ins for one account within the window before the brute-force
    #: detector raises an alert.
    SECURITY_BRUTE_FORCE_THRESHOLD: int = 5
    #: The window, in minutes, over which recent failed sign-ins are counted.
    SECURITY_FAILED_LOGIN_WINDOW_MINUTES: int = 15

    # ------------------------------------------------ observability
    # Phase 7.4. Metrics collection is in-process and always on — it is a few
    # counters, and the `/metrics` scrape is what makes the RED signals usable.
    # Tracing is opt-in and degrades to a no-op when the OpenTelemetry packages
    # are absent, so a deployment that has not wired a collector pays nothing.
    METRICS_ENABLED: bool = True
    #: Optional bearer token guarding `/metrics`. Unset leaves the endpoint open,
    #: which is the norm for a Prometheus scrape on an internal network; set it
    #: when the scrape path is exposed.
    METRICS_TOKEN: SecretStr = SecretStr("")

    #: Turn on OpenTelemetry tracing. Requires the `opentelemetry-*` packages;
    #: when they are missing the tracer is a no-op regardless of this flag, so
    #: enabling it without installing them is harmless rather than fatal.
    OTEL_ENABLED: bool = False
    OTEL_SERVICE_NAME: str = "vantage-crm-api"
    #: OTLP endpoint the span exporter ships to (e.g. http://collector:4318).
    #: Unset falls back to the OpenTelemetry SDK's own default resolution.
    OTEL_EXPORTER_OTLP_ENDPOINT: str | None = None

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
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "STRIPE_PORTAL_RETURN_URL",
        "GOOGLE_OAUTH_REDIRECT_URI",
        "POSTGRES_REPLICA_HOST",
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

    def scaling_warnings(self) -> list[str]:
        """Scaling/topology misconfigurations (Phase 8.1).

        A pure function of the settings so it is testable on its own and reusable
        by the production check and an ops preflight. Each entry is a real
        misconfiguration, not a style preference.
        """
        problems: list[str] = []
        if self.POSTGRES_REPLICA_HOST and (
            self.POSTGRES_REPLICA_HOST,
            self.POSTGRES_REPLICA_PORT,
        ) == (self.POSTGRES_HOST, self.POSTGRES_PORT):
            problems.append(
                "POSTGRES_REPLICA_HOST points at the primary — read traffic would "
                "not be offloaded. Unset it or point it at the replica endpoint"
            )
        if self.DB_POOL_SIZE + self.DB_MAX_OVERFLOW < 8:
            problems.append(
                "DB_POOL_SIZE + DB_MAX_OVERFLOW is below 8 — too little connection "
                "headroom for a horizontally scaled deployment under load"
            )
        if self.WORKER_MAX_JOBS < 1:
            problems.append("WORKER_MAX_JOBS must be at least 1")
        return problems

    def assert_production_ready(self) -> None:
        """Refuse to serve production traffic with development defaults.

        Called from the app lifespan. These are the settings that are harmless
        locally and dangerous in production, so they are checked explicitly
        rather than trusted to review.
        """
        if not self.is_production:
            return

        problems: list[str] = list(self.scaling_warnings())
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
        # Encryption at rest for secret columns. Checked rather than assumed:
        # the failure mode is silent — everything works, and the TOTP secrets
        # sit in the database in the clear waiting for the one dump that matters.
        from app.core.secrets import encryption_configured

        if not encryption_configured(self):
            problems.append(
                "Secret encryption is not configured. Set ENCRYPTION_KEYS "
                "(id:base64-key) or ENCRYPTION_PROVIDER=aws_kms with KMS_KEY_ID "
                "— without it MFA secrets are stored in plaintext"
            )
        elif self.ENCRYPTION_PROVIDER == "local":
            problems.append(
                "ENCRYPTION_PROVIDER is 'local' in production: the key lives in "
                "this process's environment, so anything that reads the "
                "environment reads the key. Use aws_kms"
            )

        if self.INTEGRATIONS_ENABLE_MOCK:
            problems.append(
                "INTEGRATIONS_ENABLE_MOCK is on in production — the mock "
                "provider pretends to sync and must never back a real "
                "connection. Turn it off"
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
        # The AI layer sends CRM data to an external model, so its production
        # guards are about egress and cost, not just correctness.
        if self.AI_ENABLED:
            if self.AI_PROVIDER == "echo":
                problems.append(
                    "AI_ENABLED is on with the 'echo' provider, which answers "
                    "nothing — configure a real AI_PROVIDER or turn the AI "
                    "layer off"
                )
            if self.AI_PROVIDER == "anthropic" and not self.AI_API_KEY.get_secret_value():
                problems.append("AI_PROVIDER is 'anthropic' but AI_API_KEY is unset")
            if self.AI_MONTHLY_COST_CEILING_USD <= 0:
                problems.append(
                    "AI_MONTHLY_COST_CEILING_USD must be a positive ceiling in "
                    "production — a disabled ceiling is unbounded model spend"
                )
        # Billing must reach its provider once it is the configured one, or a
        # tenant would be unable to subscribe with no visible reason.
        if self.BILLING_PROVIDER == "stripe":
            if not self.STRIPE_API_KEY.get_secret_value():
                problems.append("BILLING_PROVIDER is 'stripe' but STRIPE_API_KEY is unset")
            if not self.STRIPE_WEBHOOK_SECRET.get_secret_value():
                problems.append(
                    "BILLING_PROVIDER is 'stripe' but STRIPE_WEBHOOK_SECRET is unset — "
                    "webhook events would be refused and subscription status would drift"
                )

        if problems:
            raise RuntimeError("Unsafe production configuration:\n  - " + "\n  - ".join(problems))


@lru_cache
def get_settings() -> Settings:
    """Cached accessor. Import this, never instantiate Settings directly."""
    return Settings()
