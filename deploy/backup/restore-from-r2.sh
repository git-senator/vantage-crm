#!/usr/bin/env bash
# Vantage CRM — pull a backup out of Cloudflare R2 and restore it.
#
# This is the script for the bad day: the server is gone, or the database is,
# and all that is left is the R2 key. It runs anywhere Docker runs — the
# machine being restored onto need not be the machine that was lost.
#
#   List what is in the vault:
#     ./restore-from-r2.sh
#
#   Restore a specific dump into a scratch database:
#     ./restore-from-r2.sh vantage-20260810T032000Z.dump vantage_restore
#
#   Restore over production (deliberate, and it will ask):
#     ./restore-from-r2.sh vantage-20260810T032000Z.dump.age vantage
#
# Credentials come from the same R2_* keys in .env that the backup uses.
#
# Dumps ending in .age are encrypted, and this machine cannot decrypt them by
# design — the private key is deliberately not here. Supply it at restore time:
#
#     AGE_IDENTITY=/path/to/rossa-backup-key.txt ./restore-from-r2.sh <name>.age
#
# Copy the key file in, restore, then delete it. It should live in a password
# manager and on the owner's machine, not on the server that writes backups.
set -euo pipefail

VANTAGE_DIR="${VANTAGE_DIR:-/opt/vantage}"
ENV_FILE="${ENV_FILE:-${VANTAGE_DIR}/.env}"
COMPOSE_FILES="${COMPOSE_FILES:--f docker-compose.yml -f deploy/compose/docker-compose.prod.yml}"
R2_PREFIX="${R2_PREFIX:-db}"
AWSCLI_IMAGE="${AWSCLI_IMAGE:-amazon/aws-cli:2.36.19}"
PGUSER="${PGUSER:-postgres}"

cd "$VANTAGE_DIR"

if [ -f "$ENV_FILE" ]; then
  set -a
  . <(grep -E '^R2_[A-Z0-9_]+=' "$ENV_FILE" || true)
  set +a
fi
: "${R2_ACCOUNT_ID:?R2_ACCOUNT_ID missing — is R2 configured in ${ENV_FILE}?}"
: "${R2_ACCESS_KEY_ID:?R2_ACCESS_KEY_ID missing}"
: "${R2_SECRET_ACCESS_KEY:?R2_SECRET_ACCESS_KEY missing}"
: "${R2_BUCKET:?R2_BUCKET missing}"

# Overridable because the dump is fetched by a container and must land on a
# path the Docker daemon can actually see. On a Linux host /tmp is that path;
# under Docker Desktop it is a directory inside the daemon's own VM, and the
# download silently goes nowhere. Set WORK_DIR to somewhere shared when
# rehearsing this on a laptop.
WORK="${WORK_DIR:-$(mktemp -d)}"
mkdir -p "$WORK"
trap 'rm -rf "$WORK"' EXIT

r2() {
  docker run --rm \
    -e AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" \
    -e AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
    -e AWS_DEFAULT_REGION=auto \
    -e AWS_REQUEST_CHECKSUM_CALCULATION=when_required \
    -e AWS_RESPONSE_CHECKSUM_VALIDATION=when_required \
    -v "$WORK:/work" \
    "$AWSCLI_IMAGE" "$@" \
    --endpoint-url "https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
}

# ------------------------------------------------------------------ list
if [ $# -eq 0 ]; then
  echo "Backups in r2://${R2_BUCKET}/${R2_PREFIX}/ (newest last):"
  r2 s3 ls "s3://${R2_BUCKET}/${R2_PREFIX}/" | grep -v '\.sha256$' | sort
  echo
  echo "Restore one with:  $0 <name>.dump [target-database]"
  exit 0
fi

NAME="$1"
TARGET="${2:-vantage_restore}"

# Restoring over the live database is a legitimate thing to need and a terrible
# thing to do by accident, so it is allowed but never silent.
if [ "$TARGET" = "vantage" ] && [ "${FORCE:-0}" != "1" ]; then
  echo "About to restore '${NAME}' OVER the live database 'vantage'."
  echo "Everything written since that dump was taken will be gone."
  printf 'Type the word RESTORE to continue: '
  read -r reply
  [ "$reply" = "RESTORE" ] || { echo "Aborted."; exit 2; }
fi

echo "Fetching ${NAME} from R2"
r2 s3 cp "s3://${R2_BUCKET}/${R2_PREFIX}/${NAME}"        "/work/${NAME}"
r2 s3 cp "s3://${R2_BUCKET}/${R2_PREFIX}/${NAME}.sha256" "/work/${NAME}.sha256" || {
  echo "WARNING: no checksum stored alongside this dump; cannot verify it." >&2
}

if [ -f "${WORK}/${NAME}.sha256" ]; then
  echo "Verifying checksum"
  ( cd "$WORK" && sha256sum -c "${NAME}.sha256" )
fi

# ---------------------------------------------------------------- decrypt
# The checksum above is taken over the ciphertext, which is what was stored and
# therefore what can be verified against tampering in transit. Decryption comes
# after: age fails loudly on a corrupted or wrong-key file, so a silent bad
# restore is not on the table.
DUMP="${WORK}/${NAME}"
case "$NAME" in
  *.age)
    if [ -z "${AGE_IDENTITY:-}" ]; then
      echo >&2
      echo "This backup is encrypted and the private key is not on this machine —" >&2
      echo "which is the point: a stolen R2 token buys ciphertext, nothing more." >&2
      echo >&2
      echo "Restore it with the key file the owner holds:" >&2
      echo "  AGE_IDENTITY=/path/to/rossa-backup-key.txt $0 ${NAME} ${TARGET}" >&2
      exit 3
    fi
    [ -f "$AGE_IDENTITY" ] || { echo "No such key file: ${AGE_IDENTITY}" >&2; exit 3; }
    command -v age >/dev/null 2>&1 || { echo "'age' is not installed here." >&2; exit 3; }
    echo "Decrypting"
    age -d -i "$AGE_IDENTITY" -o "${WORK}/${NAME%.age}" "${WORK}/${NAME}"
    DUMP="${WORK}/${NAME%.age}"
    ;;
esac

# shellcheck disable=SC2086
COMPOSE="docker compose ${COMPOSE_FILES} --env-file .env"

echo "Restoring into database '${TARGET}'"
$COMPOSE exec -T postgres psql -U "$PGUSER" -c "CREATE DATABASE ${TARGET};" 2>/dev/null ||
  echo "  (database ${TARGET} already exists — restoring into it)"

$COMPOSE exec -T postgres pg_restore -U "$PGUSER" --dbname="$TARGET" \
  --no-owner --no-privileges --clean --if-exists < "$DUMP"

echo
echo "Restored. What is in there now:"
$COMPOSE exec -T postgres psql -U "$PGUSER" -d "$TARGET" -t -c \
  "SELECT 'users: '||(SELECT count(*) FROM users)
        ||' | properties: '||(SELECT count(*) FROM properties)
        ||' | leads: '||(SELECT count(*) FROM leads)
        ||' | schema: '||(SELECT version_num FROM alembic_version);"
