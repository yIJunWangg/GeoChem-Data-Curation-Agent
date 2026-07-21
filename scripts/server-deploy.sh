#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${GEOCHEM_ENV_FILE:-$ROOT_DIR/deploy/.env}"
COMPOSE_FILE="$ROOT_DIR/deploy/docker-compose.yml"
MODE="staging"
REQUIRE_BACKUP="false"
SKIP_BUILD="false"
VERIFY_ONLY="false"
LOCK_DIR="${TMPDIR:-/tmp}/geochem-deploy.lock"

usage() {
  cat <<'EOF'
Usage: scripts/server-deploy.sh [options]

Options:
  --production       Enforce the Ubuntu production host requirements.
  --require-backup   Require the NAS/restic backup target during preflight.
  --skip-build       Reuse the image already present on the host.
  --verify-only      Do not change the stack; only run post-deploy checks.
  -h, --help         Show this help.

Production mode implies --require-backup. Existing production installations
are backed up before a maintenance-window migration. The script never deletes
application data and always runs Alembic before replacing application services.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --production) MODE="production"; REQUIRE_BACKUP="true" ;;
    --require-backup) REQUIRE_BACKUP="true" ;;
    --skip-build) SKIP_BUILD="true" ;;
    --verify-only) VERIFY_ONLY="true" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "Server deployment requires Linux. Use scripts/mac-preview.sh on macOS." >&2
  exit 1
fi

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "Another GeoChem deployment is already running." >&2
  exit 1
fi
trap 'rmdir "$LOCK_DIR"' EXIT

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Creating the deployment environment and persistent directories first."
  GEOCHEM_ENV_FILE="$ENV_FILE" "$ROOT_DIR/scripts/server-bootstrap.sh"
  echo "Review $ENV_FILE and add a shared LLM key, then run this command again." >&2
  exit 2
fi

preflight_args=()
[[ "$MODE" == "production" ]] && preflight_args+=(--production)
[[ "$REQUIRE_BACKUP" == "true" ]] && preflight_args+=(--require-backup)
GEOCHEM_ENV_FILE="$ENV_FILE" "$ROOT_DIR/scripts/server-preflight.sh" "${preflight_args[@]}"

if [[ "$VERIFY_ONLY" == "true" ]]; then
  GEOCHEM_ENV_FILE="$ENV_FILE" "$ROOT_DIR/scripts/server-verify.sh"
  exit $?
fi

compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
existing_postgres_id="$("${compose[@]}" ps -q postgres 2>/dev/null || true)"
existing_web_id="$("${compose[@]}" ps -q web 2>/dev/null || true)"

if [[ "$SKIP_BUILD" != "true" ]]; then
  echo "Building the GeoChem application image..."
  "${compose[@]}" build web worker migrate
fi

echo "Starting PostgreSQL, Redis and Keycloak..."
"${compose[@]}" up -d --wait --wait-timeout 360 postgres redis keycloak

if [[ "$MODE" == "production" && -n "$existing_postgres_id" ]]; then
  if [[ "${GEOCHEM_SKIP_PREDEPLOY_BACKUP:-false}" == "true" ]]; then
    echo "WARNING: pre-deployment backup was explicitly skipped." >&2
  else
    echo "Backing up the existing production installation before migration..."
    GEOCHEM_ENV_FILE="$ENV_FILE" "$ROOT_DIR/scripts/backup-server.sh"
  fi
fi

echo "Synchronizing the Keycloak Web client with the configured LAN hostname..."
GEOCHEM_ENV_FILE="$ENV_FILE" "$ROOT_DIR/scripts/configure-keycloak-client.sh"

if [[ -n "$existing_web_id" ]]; then
  echo "Entering the application maintenance window..."
  "${compose[@]}" stop caddy web worker
fi

echo "Applying database migrations..."
"${compose[@]}" run --rm migrate

echo "Starting Web, worker and Caddy..."
"${compose[@]}" up -d web worker caddy

GEOCHEM_ENV_FILE="$ENV_FILE" "$ROOT_DIR/scripts/server-verify.sh"

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
echo
echo "GeoChem is ready at https://${GEOCHEM_SITE_HOST}"
echo "In-app user administration: https://${GEOCHEM_SITE_HOST}/admin/users"
echo "Initial application administrator: ${GEOCHEM_INITIAL_ADMIN_USERNAME:-geochem-admin}"
echo "The first-login temporary password remains in the protected deployment environment."
echo "Keycloak break-glass console: https://${GEOCHEM_AUTH_HOST}"
echo "Install the local CA exported by: scripts/export-caddy-ca.sh"
