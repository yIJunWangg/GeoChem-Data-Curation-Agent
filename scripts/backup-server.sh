#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${GEOCHEM_ENV_FILE:-$ROOT_DIR/deploy/.env}"
COMPOSE_FILE="$ROOT_DIR/deploy/docker-compose.yml"
LOCK_DIR="${TMPDIR:-/tmp}/geochem-backup.lock"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "Another GeoChem backup is already running." >&2
  exit 1
fi
trap 'rmdir "$LOCK_DIR"' EXIT

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing deployment environment: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

for command_name in docker restic mountpoint; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing required command: $command_name" >&2
    exit 1
  fi
done

if ! mountpoint -q "$GEOCHEM_NAS_MOUNT"; then
  echo "NAS is not mounted at $GEOCHEM_NAS_MOUNT; backup aborted." >&2
  exit 1
fi

if [[ ! -r "$RESTIC_PASSWORD_FILE" ]]; then
  echo "Restic password file is not readable: $RESTIC_PASSWORD_FILE" >&2
  exit 1
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
dump_dir="$GEOCHEM_DATA_PATH/backups/postgres/$timestamp"
mkdir -p "$dump_dir"

compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
"${compose[@]}" exec -T postgres pg_dump -U postgres -Fc geochem >"$dump_dir/geochem.dump"
"${compose[@]}" exec -T postgres pg_dump -U postgres -Fc keycloak >"$dump_dir/keycloak.dump"
for dump_file in "$dump_dir/geochem.dump" "$dump_dir/keycloak.dump"; do
  if [[ ! -s "$dump_file" ]]; then
    echo "PostgreSQL dump is empty: $dump_file" >&2
    exit 1
  fi
done
"${compose[@]}" ps --format json >"$dump_dir/compose-services.json"

export RESTIC_REPOSITORY RESTIC_PASSWORD_FILE
if ! restic snapshots >/dev/null 2>&1; then
  restic init
fi

restic backup \
  --tag geochem-server \
  --host "$(hostname)" \
  "$GEOCHEM_DATA_PATH" \
  "$CADDY_DATA_PATH" \
  "$CADDY_CONFIG_PATH"

restic forget \
  --tag geochem-server \
  --keep-daily "${BACKUP_RETENTION_DAILY:-7}" \
  --keep-weekly "${BACKUP_RETENTION_WEEKLY:-5}" \
  --keep-monthly "${BACKUP_RETENTION_MONTHLY:-12}" \
  --prune
restic check

# Restic now owns the long-term history; retain only two days of loose dumps.
find "$GEOCHEM_DATA_PATH/backups/postgres" -mindepth 1 -maxdepth 1 \
  -type d -mtime +2 -exec rm -rf -- {} +

echo "GeoChem backup completed: $timestamp"
