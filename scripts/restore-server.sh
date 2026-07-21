#!/usr/bin/env bash
set -euo pipefail

if [[ "${CONFIRM_RESTORE:-}" != "YES" ]]; then
  echo "Restore is destructive. Re-run with CONFIRM_RESTORE=YES." >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${GEOCHEM_ENV_FILE:-$ROOT_DIR/deploy/.env}"
COMPOSE_FILE="$ROOT_DIR/deploy/docker-compose.yml"
SNAPSHOT="${RESTIC_SNAPSHOT:-latest}"
LOCK_DIR="${TMPDIR:-/tmp}/geochem-restore.lock"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "Another GeoChem restore is already running." >&2
  exit 1
fi
restore_root=""
cleanup() {
  [[ -z "$restore_root" ]] || rm -rf "$restore_root"
  rmdir "$LOCK_DIR"
}
trap cleanup EXIT

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing deployment environment: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
export RESTIC_REPOSITORY RESTIC_PASSWORD_FILE

for command_name in docker restic rsync find sort; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing required command: $command_name" >&2
    exit 1
  fi
done

restore_root="$(mktemp -d "${TMPDIR:-/tmp}/geochem-restore.XXXXXX")"

restic restore "$SNAPSHOT" --target "$restore_root"

restored_data="$restore_root$GEOCHEM_DATA_PATH"
restored_caddy_data="$restore_root$CADDY_DATA_PATH"
restored_caddy_config="$restore_root$CADDY_CONFIG_PATH"
if [[ ! -d "$restored_data" ]]; then
  echo "Snapshot does not contain $GEOCHEM_DATA_PATH" >&2
  exit 1
fi

restored_dump_dir="$(find "$restored_data/backups/postgres" -mindepth 2 -maxdepth 2 \
  -name geochem.dump -type f -printf '%T@ %h\n' | sort -nr | head -n1 | cut -d' ' -f2-)"
if [[ -z "$restored_dump_dir" ]]; then
  echo "Snapshot does not contain a PostgreSQL backup set." >&2
  exit 1
fi
for database_name in geochem keycloak; do
  if [[ ! -s "$restored_dump_dir/$database_name.dump" ]]; then
    echo "Snapshot database dump is missing or empty: $restored_dump_dir/$database_name.dump" >&2
    exit 1
  fi
done
dump_set_name="$(basename "$restored_dump_dir")"

compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
"${compose[@]}" stop caddy web worker keycloak redis

rsync -a --delete "$restored_data/" "$GEOCHEM_DATA_PATH/"
[[ -d "$restored_caddy_data" ]] && rsync -a --delete "$restored_caddy_data/" "$CADDY_DATA_PATH/"
[[ -d "$restored_caddy_config" ]] && rsync -a --delete "$restored_caddy_config/" "$CADDY_CONFIG_PATH/"
chown -R 10001:10001 "$GEOCHEM_DATA_PATH"

latest_dump="$GEOCHEM_DATA_PATH/backups/postgres/$dump_set_name"

"${compose[@]}" up -d postgres
until "${compose[@]}" exec -T postgres pg_isready -U postgres -d postgres >/dev/null 2>&1; do
  sleep 2
done

for database_name in geochem keycloak; do
  owner="$database_name"
  if [[ "$database_name" == "geochem" ]]; then
    owner_password="$GEOCHEM_DB_PASSWORD"
  else
    owner_password="$KEYCLOAK_DB_PASSWORD"
  fi
  dump_file="$latest_dump/$database_name.dump"
  if [[ ! -s "$dump_file" ]]; then
    echo "PostgreSQL dump is missing or empty: $dump_file" >&2
    exit 1
  fi
  "${compose[@]}" exec -T postgres psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${database_name}' AND pid <> pg_backend_pid();" \
    -c "DROP DATABASE IF EXISTS ${database_name};" \
    -c "CREATE DATABASE ${database_name} OWNER ${owner} ENCODING 'UTF8';"
  "${compose[@]}" exec -e PGPASSWORD="$owner_password" -T postgres \
    pg_restore -h 127.0.0.1 -U "$owner" -d "$database_name" --clean --if-exists --no-owner \
    <"$dump_file"
done

"${compose[@]}" run --rm migrate
"${compose[@]}" up -d --wait --wait-timeout 360
GEOCHEM_ENV_FILE="$ENV_FILE" "$ROOT_DIR/scripts/configure-keycloak-client.sh"
GEOCHEM_ENV_FILE="$ENV_FILE" "$ROOT_DIR/scripts/server-verify.sh"
echo "GeoChem restored and verified from restic snapshot $SNAPSHOT."
