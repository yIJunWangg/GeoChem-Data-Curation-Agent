#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${GEOCHEM_ENV_FILE:-$ROOT_DIR/deploy/.env}"
COMPOSE_FILE="$ROOT_DIR/deploy/docker-compose.yml"
SECRET_DIR="$ROOT_DIR/deploy/secrets"
CREDENTIAL_MASTER_KEY_FILE="$SECRET_DIR/credential_master_key"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This bootstrap script targets the WSL2/Ubuntu deployment host." >&2
  echo "Use scripts/mac-preview.sh for the local Mac preview." >&2
  exit 1
fi

for command_name in docker openssl; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing required command: $command_name" >&2
    exit 1
  fi
done

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required." >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ROOT_DIR/deploy/.env.example" "$ENV_FILE"
  for variable in \
    POSTGRES_SUPERUSER_PASSWORD \
    GEOCHEM_DB_PASSWORD \
    KEYCLOAK_DB_PASSWORD \
    REDIS_PASSWORD \
    KEYCLOAK_ADMIN_PASSWORD \
    KEYCLOAK_ADMIN_API_CLIENT_SECRET \
    GEOCHEM_INITIAL_ADMIN_PASSWORD; do
    value="$(openssl rand -hex 24)"
    sed -i "s|^${variable}=.*|${variable}=${value}|" "$ENV_FILE"
  done
  chmod 600 "$ENV_FILE"
  echo "Created $ENV_FILE with generated infrastructure passwords."
  echo "Initial GeoChem administrator: ${GEOCHEM_INITIAL_ADMIN_USERNAME:-geochem-admin}"
  echo "Its temporary password is stored only in the mode-600 deployment environment."
  echo "Add the shared LLM API key before starting the stack."
fi

mkdir -p "$SECRET_DIR"
if [[ ! -f "$CREDENTIAL_MASTER_KEY_FILE" ]]; then
  openssl rand -hex 32 > "$CREDENTIAL_MASTER_KEY_FILE"
  chmod 600 "$CREDENTIAL_MASTER_KEY_FILE"
  echo "Created encrypted-model credential master key: $CREDENTIAL_MASTER_KEY_FILE"
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
QDRANT_DATA_PATH="${QDRANT_DATA_PATH:-/srv/geochem/qdrant}"

for path in \
  "$GEOCHEM_DATA_PATH/config" \
  "$GEOCHEM_DATA_PATH/workspaces" \
  "$GEOCHEM_DATA_PATH/exports" \
  "$GEOCHEM_DATA_PATH/backups/postgres" \
  "$POSTGRES_DATA_PATH" \
  "$REDIS_DATA_PATH" \
  "$QDRANT_DATA_PATH" \
  "$CADDY_DATA_PATH" \
  "$CADDY_CONFIG_PATH"; do
  sudo mkdir -p "$path"
done

# The application image runs with a stable non-root uid. Database, Redis and
# Caddy entrypoints retain ownership of their own host directories.
sudo chown -R 10001:10001 "$GEOCHEM_DATA_PATH"
sudo chown -R 1000:1000 "$QDRANT_DATA_PATH"

if [[ ! -f "$RESTIC_PASSWORD_FILE" ]]; then
  sudo mkdir -p "$(dirname "$RESTIC_PASSWORD_FILE")"
  openssl rand -hex 32 | sudo tee "$RESTIC_PASSWORD_FILE" >/dev/null
  sudo chmod 600 "$RESTIC_PASSWORD_FILE"
  echo "Created restic password file: $RESTIC_PASSWORD_FILE"
fi

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config --quiet
echo "GeoChem deployment configuration is valid."

if [[ "${1:-}" == "--start" ]]; then
  echo "Delegating startup to the verified deployment workflow..."
  GEOCHEM_ENV_FILE="$ENV_FILE" "$ROOT_DIR/scripts/server-deploy.sh"
fi
