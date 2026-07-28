# Deployment (Phase 8.1)

Production-hardened deployment artifacts for Vantage CRM.

## Contents

- `helm/vantage-crm/` — Helm chart for the API and worker. Multiple replicas,
  HPAs, PodDisruptionBudgets, non-root read-only-rootfs containers, liveness on
  `/api/v1/health/live` and readiness on `/api/v1/health/ready`, and graceful
  drain via a preStop hook + termination grace periods (long for the worker so a
  running job finishes rather than dead-lettering).
- `backup/backup.sh`, `backup/restore.sh` — logical backup and restore with a
  checksum and a production-overwrite guard.
- `DISASTER_RECOVERY.md` — RPO/RTO targets and runbooks (replica promotion, PITR,
  logical restore).
- `../load/locustfile.py` — read-heavy load/stress profile for the public API.

## Scaling model

- **Stateless API** scales horizontally behind the Service; the only shared state
  is PostgreSQL and Redis. Set `config.POSTGRES_REPLICA_HOST` to offload reporting
  reads to a replica (`read_session_scope`); the readiness probe then includes a
  `read_database` check.
- **Workers** scale independently. Priority queues (`app/core/partitioning.py`)
  keep latency-sensitive jobs ahead of nightly sweeps, and tenant sharding
  (`shard_for`) lets a sweep be split across worker replicas so no single worker
  walks every tenant.
- **Cache-stampede protection** (`app/core/cache.py`) single-flights hot
  recomputes with the existing Redis lock and jittered TTLs, so a scale-out event
  does not turn a cache miss into a herd.

## Preflight

`Settings.scaling_warnings()` is checked at startup in production
(`assert_production_ready`) — a replica pointed at the primary, or too small a
connection pool for the fleet, fails the deploy rather than surfacing under load.

Secrets (DB password, JWT secret, encryption keys, Stripe keys) are supplied via
the externally-managed Secret named by `existingSecret`; the chart never renders
a secret from values.
