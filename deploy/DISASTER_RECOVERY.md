# Disaster Recovery (Phase 8.1)

Objectives:

| Metric | Target |
| ------ | ------ |
| RPO (max data loss) | ≤ 5 minutes (continuous WAL archiving) |
| RTO (time to restore service) | ≤ 30 minutes (replica promotion) or ≤ 2 hours (logical restore) |

## Layers of protection

1. **Streaming replica** (`POSTGRES_REPLICA_HOST`). A hot standby that also
   serves read traffic (see `app/db/session.py::read_session_scope`). On primary
   loss it is promoted; the app is repointed by swapping the primary DSN. This is
   the fast path and the lowest RPO.
2. **Continuous WAL archiving** to object storage gives point-in-time recovery
   between base backups — the true RPO driver.
3. **Nightly logical backups** (`deploy/backup/backup.sh`) — a portable,
   custom-format `pg_dump` with a checksum, retained for `RETENTION_DAYS`. The
   coarse fallback and the source for non-prod refreshes.

## Runbooks

### Primary failure → promote replica
1. Confirm the primary is truly gone (not a network partition).
2. `pg_ctl promote` (or the managed-service failover) on the replica.
3. Update the primary DSN secret; unset `POSTGRES_REPLICA_HOST` until a new
   standby is built. Roll the API and worker deployments.
4. Verify `/api/v1/health/ready` reports `ready`.

### Full logical restore
1. Provision an empty database whose name ends in `_restore`.
2. `ARTIFACT_URI=s3://.../<stamp>/<file>.dump PGDATABASE=vantage_restore \
   deploy/backup/restore.sh` (the suffix guard prevents an accidental prod
   overwrite; `FORCE=1` for an intentional one).
3. Run `alembic upgrade head` to reconcile schema, then cut over.

## Verification

- **Restore drills are quarterly**, not theoretical: a backup that has never
  been restored is a hope, not a plan.
- RLS survives a restore — policies are DDL created by the migrations and are
  captured by `pg_dump`; confirm `FORCE ROW LEVEL SECURITY` is present on tenant
  tables after any restore before serving traffic.
