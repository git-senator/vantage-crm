#!/usr/bin/env bash
# Vantage CRM — restore a logical backup into a target database (Phase 8.1).
#
# Verifies the checksum, then restores with pg_restore. Refuses to run against a
# database whose name does not end in the required suffix unless FORCE=1, so a
# restore cannot overwrite production by a fat-fingered PGDATABASE.
set -euo pipefail

: "${PGHOST:?PGHOST is required}"
: "${PGUSER:?PGUSER is required}"
: "${PGDATABASE:?PGDATABASE is required}"
: "${ARTIFACT_URI:?ARTIFACT_URI is required}"   # s3://.../<stamp>/<file>.dump
PGPORT="${PGPORT:-5432}"
REQUIRE_SUFFIX="${REQUIRE_SUFFIX:-_restore}"

if [ "${FORCE:-0}" != "1" ] && [[ "$PGDATABASE" != *"$REQUIRE_SUFFIX" ]]; then
  echo "Refusing: PGDATABASE '${PGDATABASE}' does not end in '${REQUIRE_SUFFIX}'." >&2
  echo "Set FORCE=1 to override (e.g. an intentional production restore)." >&2
  exit 2
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
artifact="${tmp}/$(basename "$ARTIFACT_URI")"

echo "Fetching ${ARTIFACT_URI}"
aws s3 cp "$ARTIFACT_URI"          "$artifact"
aws s3 cp "${ARTIFACT_URI}.sha256" "${artifact}.sha256" || true

if [ -f "${artifact}.sha256" ]; then
  echo "Verifying checksum"
  ( cd "$tmp" && sha256sum -c "$(basename "${artifact}.sha256")" )
fi

echo "Restoring into ${PGDATABASE}@${PGHOST}:${PGPORT}"
pg_restore \
  --host="$PGHOST" --port="$PGPORT" --username="$PGUSER" \
  --dbname="$PGDATABASE" --no-owner --no-privileges \
  --clean --if-exists --jobs="${RESTORE_JOBS:-4}" "$artifact"

echo "Restore complete."
