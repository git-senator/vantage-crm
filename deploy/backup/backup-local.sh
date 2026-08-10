#!/usr/bin/env bash
# Vantage CRM — nightly database backup for the single-VPS deploy.
#
# Two copies, because one is none:
#
#   * a local dump in /var/backups/vantage, kept 14 days. This is the fast
#     restore for "the migration ate a table this morning" — no network, no
#     credentials, seconds to hand to pg_restore.
#
#   * an off-site copy in Cloudflare R2, kept 30 days. This is the copy that
#     outlives the server. A backup stored only on the machine it protects is
#     not a backup: the disk failure, the ransomware and the accidental
#     `docker volume rm` all take both at once.
#
# The aws CLI runs in a container rather than being installed on the host: the
# version is pinned instead of being whatever the distro ships, and the host
# keeps no Python it does not otherwise need. Docker is already a dependency of
# everything else here, so this adds nothing to the machine.
#
# Configuration lives in /opt/vantage/.env (R2_* keys). Leave those out and the
# script still takes the local dump and says clearly that it is the only copy —
# an unconfigured off-site is a warning, not a crash. A *failed* off-site, on
# the other hand, is an error: it means we believed we had two copies.
#
# Every path below has a production default and an override, so the script can
# be rehearsed end to end against a local stack and a scratch prefix before it
# is trusted with the real thing. A backup script whose first execution is also
# its first test is how empty buckets happen.
set -euo pipefail

VANTAGE_DIR="${VANTAGE_DIR:-/opt/vantage}"
DIR="${BACKUP_DIR:-/var/backups/vantage}"
ENV_FILE="${ENV_FILE:-${VANTAGE_DIR}/.env}"
COMPOSE_FILES="${COMPOSE_FILES:--f docker-compose.yml -f deploy/compose/docker-compose.prod.yml}"
R2_PREFIX="${R2_PREFIX:-db}"
LOCAL_RETENTION_DAYS="${LOCAL_RETENTION_DAYS:-14}"
OFFSITE_RETENTION_DAYS="${OFFSITE_RETENTION_DAYS:-30}"
MIN_DUMP_BYTES="${MIN_DUMP_BYTES:-100000}"
# Pinned, not :latest. This runs unattended at 03:20; a silently-upgraded CLI
# that changes its checksum defaults would break the upload on a night nobody
# is watching. There is no floating ":2" tag, so the version is exact.
AWSCLI_IMAGE="${AWSCLI_IMAGE:-amazon/aws-cli:2.36.19}"

cd "$VANTAGE_DIR"
mkdir -p "$DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
NAME="vantage-${STAMP}.dump"
ART="$DIR/$NAME"

# ------------------------------------------------------------------ dump
# shellcheck disable=SC2086  # COMPOSE_FILES is a deliberate word list
COMPOSE="docker compose ${COMPOSE_FILES} --env-file .env"

# Written to .part first. A dump interrupted half-way is a plausible-looking
# file of the right name and the wrong contents — the kind of backup you only
# discover is broken on the day you need it. Only a pg_dump that exited 0 earns
# the real name.
$COMPOSE exec -T postgres pg_dump -U postgres --format=custom --compress=9 \
  --no-owner --no-privileges vantage > "${ART}.part"
mv "${ART}.part" "$ART"

( cd "$DIR" && sha256sum "$NAME" > "${NAME}.sha256" )

SIZE=$(stat -c %s "$ART")
echo "dump ok: $ART ($(numfmt --to=iec "$SIZE"))"

# A dump far smaller than the last one usually means pg_dump connected, failed
# to see the tables and wrote an empty-but-valid archive. Cheap to check.
if [ "$SIZE" -lt "$MIN_DUMP_BYTES" ]; then
  echo "REFUSING: dump is only ${SIZE} bytes — that is not a real database" >&2
  exit 1
fi

find "$DIR" -name 'vantage-*.dump' -mtime +"$LOCAL_RETENTION_DAYS" -delete
find "$DIR" -name 'vantage-*.dump.sha256' -mtime +"$LOCAL_RETENTION_DAYS" -delete
find "$DIR" -name 'vantage-*.dump.part' -mtime +1 -delete

# --------------------------------------------------------------- off-site
if [ -f "$ENV_FILE" ]; then
  set -a
  # Only the backup keys, not the whole environment file: sourcing that
  # wholesale would drag in every application secret and any stray shell
  # metacharacter with it.
  . <(grep -E '^(R2_[A-Z0-9_]+|BACKUP_AGE_RECIPIENT)=' "$ENV_FILE" || true)
  set +a
fi

if [ -z "${R2_ACCOUNT_ID:-}" ] || [ -z "${R2_ACCESS_KEY_ID:-}" ] ||
   [ -z "${R2_SECRET_ACCESS_KEY:-}" ] || [ -z "${R2_BUCKET:-}" ]; then
  echo "WARNING: R2 is not configured — this dump exists only on this server." >&2
  echo "         Set R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY /" >&2
  echo "         R2_BUCKET in ${ENV_FILE} to keep an off-site copy." >&2
  exit 0
fi

# ------------------------------------------------------- encrypt for off-site
# The local copy stays as-is: it is on a root-only path on the machine that
# already holds the live database, so encrypting it would protect nothing and
# would slow down the restore you actually reach for.
#
# The off-site copy is different. It leaves the building. Cloudflare encrypts
# objects at rest, but with *their* key — which means the bucket's contents are
# readable by anyone holding the R2 token, and that token lives in a file on
# this server and has been handled by humans. Encrypting to an age recipient
# changes what a stolen token is worth: the thief gets ciphertext, because the
# private key deliberately does not exist on this machine.
#
# The consequence is deliberate and must be understood before trusting it: this
# server can WRITE backups it cannot READ. Losing the private key means losing
# every off-site copy. It belongs in a password manager, not only on a laptop.
UPLOAD_NAME="$NAME"

if [ -n "${BACKUP_AGE_RECIPIENT:-}" ]; then
  if ! command -v age >/dev/null 2>&1; then
    echo "FAILED: BACKUP_AGE_RECIPIENT is set but 'age' is not installed." >&2
    echo "        apt-get install -y age — refusing to upload in the clear" >&2
    echo "        when the configuration says the copy should be encrypted." >&2
    exit 1
  fi
  echo "encrypting for ${BACKUP_AGE_RECIPIENT}"
  age -r "$BACKUP_AGE_RECIPIENT" -o "${ART}.age" "$ART"
  UPLOAD_NAME="${NAME}.age"
  ( cd "$DIR" && sha256sum "$UPLOAD_NAME" > "${UPLOAD_NAME}.sha256" )
  SIZE=$(stat -c %s "${DIR}/${UPLOAD_NAME}")
else
  echo "WARNING: BACKUP_AGE_RECIPIENT is not set — the off-site copy will be" >&2
  echo "         readable by anyone who obtains the R2 token." >&2
fi

r2() {
  docker run --rm \
    -e AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" \
    -e AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
    -e AWS_DEFAULT_REGION=auto \
    -e AWS_REQUEST_CHECKSUM_CALCULATION=when_required \
    -e AWS_RESPONSE_CHECKSUM_VALIDATION=when_required \
    -v "$DIR:/backups" \
    "$AWSCLI_IMAGE" "$@" \
    --endpoint-url "https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
}

echo "uploading to r2://${R2_BUCKET}/${R2_PREFIX}/${UPLOAD_NAME}"
r2 s3 cp "/backups/${UPLOAD_NAME}"        "s3://${R2_BUCKET}/${R2_PREFIX}/${UPLOAD_NAME}"
r2 s3 cp "/backups/${UPLOAD_NAME}.sha256" "s3://${R2_BUCKET}/${R2_PREFIX}/${UPLOAD_NAME}.sha256"

# Ask R2 what it actually stored. `aws s3 cp` reporting success is not the same
# as the object being there at the right length, and the whole point of the
# off-site copy is that nobody looks at it until the day it has to work.
# Matched on the exact name: `s3 ls <key>` is a prefix search, so it also
# returns <key>.sha256 and an unfiltered read would compare against two sizes.
REMOTE_SIZE=$(r2 s3 ls "s3://${R2_BUCKET}/${R2_PREFIX}/${UPLOAD_NAME}" |
  awk -v n="$UPLOAD_NAME" '$4 == n {print $3}')
if [ "$REMOTE_SIZE" != "$SIZE" ]; then
  echo "FAILED: R2 holds ${REMOTE_SIZE:-nothing} bytes, expected ${SIZE}" >&2
  exit 1
fi
echo "offsite ok: r2://${R2_BUCKET}/${R2_PREFIX}/${UPLOAD_NAME} (${REMOTE_SIZE} bytes, verified)"

# The ciphertext was only ever a shipping container. Now that R2 has confirmed
# it holds the object, keeping a local copy this machine cannot decrypt would
# be storage spent on a file of no use to anyone standing here.
if [ "$UPLOAD_NAME" != "$NAME" ]; then
  rm -f "${DIR}/${UPLOAD_NAME}" "${DIR}/${UPLOAD_NAME}.sha256"
fi

# ------------------------------------------------------- off-site rotation
# Keys are vantage-YYYYmmddTHHMMSSZ.dump, so the date sorts lexically and the
# cutoff is a string comparison — no date parsing of remote listings.
CUTOFF=$(date -u -d "-${OFFSITE_RETENTION_DAYS} days" +%Y%m%d)
r2 s3 ls "s3://${R2_BUCKET}/${R2_PREFIX}/" | awk '{print $4}' | while read -r key; do
  [ -z "$key" ] && continue
  # vantage-20260810T032000Z.dump -> 20260810
  day=${key#vantage-}
  day=${day%%T*}
  case "$day" in
    [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) ;;
    *) continue ;;
  esac
  if [ "$day" \< "$CUTOFF" ]; then
    echo "  pruning r2://${R2_BUCKET}/${R2_PREFIX}/${key}"
    r2 s3 rm "s3://${R2_BUCKET}/${R2_PREFIX}/${key}" >/dev/null
  fi
done

echo "backup complete."
