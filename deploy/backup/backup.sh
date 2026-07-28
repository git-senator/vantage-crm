#!/usr/bin/env bash
# Vantage CRM — logical backup of the primary database (Phase 8.1).
#
# Takes a compressed, custom-format pg_dump suitable for selective restore, and
# uploads it to object storage with a timestamped key. Run on a schedule
# (CronJob) against the PRIMARY, never a replica mid-recovery.
#
# Point-in-time recovery is provided separately by continuous WAL archiving
# (see DISASTER_RECOVERY.md); this logical dump is the coarse, portable fallback
# and the source for non-prod refreshes.
set -euo pipefail

: "${PGHOST:?PGHOST is required}"
: "${PGUSER:?PGUSER is required}"
: "${PGDATABASE:?PGDATABASE is required}"
: "${BACKUP_BUCKET:?BACKUP_BUCKET is required}"   # e.g. s3://vantage-backups
PGPORT="${PGPORT:-5432}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
artifact="vantage-${PGDATABASE}-${stamp}.dump"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "Dumping ${PGDATABASE}@${PGHOST}:${PGPORT} -> ${artifact}"
pg_dump \
  --host="$PGHOST" --port="$PGPORT" --username="$PGUSER" \
  --format=custom --compress=9 --no-owner --no-privileges \
  --file="${tmp}/${artifact}" "$PGDATABASE"

sha256sum "${tmp}/${artifact}" > "${tmp}/${artifact}.sha256"

echo "Uploading to ${BACKUP_BUCKET}/${stamp}/"
aws s3 cp "${tmp}/${artifact}"        "${BACKUP_BUCKET}/${stamp}/${artifact}"        --sse AES256
aws s3 cp "${tmp}/${artifact}.sha256" "${BACKUP_BUCKET}/${stamp}/${artifact}.sha256" --sse AES256

echo "Pruning backups older than ${RETENTION_DAYS} days"
cutoff="$(date -u -d "-${RETENTION_DAYS} days" +%Y%m%d 2>/dev/null || date -u -v-"${RETENTION_DAYS}"d +%Y%m%d)"
aws s3 ls "${BACKUP_BUCKET}/" | awk '{print $2}' | tr -d '/' | while read -r prefix; do
  [ -z "$prefix" ] && continue
  if [ "${prefix%T*}" \< "$cutoff" ]; then
    echo "  removing ${prefix}"
    aws s3 rm "${BACKUP_BUCKET}/${prefix}/" --recursive
  fi
done

echo "Backup complete: ${BACKUP_BUCKET}/${stamp}/${artifact}"
