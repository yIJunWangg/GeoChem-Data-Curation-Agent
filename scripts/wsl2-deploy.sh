#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${GEOCHEM_ENV_FILE:-$ROOT_DIR/deploy/.env}"
SKIP_BUILD="false"
VERIFY_ONLY="false"

usage() {
  cat <<'EOF'
Usage: scripts/wsl2-deploy.sh [--skip-build] [--verify-only]

Runs the complete GeoChem LAN staging stack inside Ubuntu WSL2. After the
stack passes verification, run the printed PowerShell command as Administrator
to trust the local CA and map geochem.lan/auth.geochem.lan on Windows.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build) SKIP_BUILD="true" ;;
    --verify-only) VERIFY_ONLY="true" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [[ "$(uname -s)" != "Linux" ]] || ! grep -qi microsoft /proc/version 2>/dev/null; then
  echo "This command must run inside Ubuntu WSL2." >&2
  exit 1
fi
if [[ "$ROOT_DIR" == /mnt/* ]]; then
  echo "Move the repository into the WSL2 Linux filesystem before deployment." >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  GEOCHEM_ENV_FILE="$ENV_FILE" "$ROOT_DIR/scripts/server-bootstrap.sh"
  echo
  echo "Review $ENV_FILE. Model credentials can be added securely in the administrator portal after login."
  echo "Re-run this command after reviewing the protected environment file."
  exit 2
fi

deploy_args=()
[[ "$SKIP_BUILD" == "true" ]] && deploy_args+=(--skip-build)
[[ "$VERIFY_ONLY" == "true" ]] && deploy_args+=(--verify-only)
GEOCHEM_ENV_FILE="$ENV_FILE" "$ROOT_DIR/scripts/server-deploy.sh" "${deploy_args[@]}"

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

GEOCHEM_ENV_FILE="$ENV_FILE" "$ROOT_DIR/scripts/export-caddy-ca.sh"

report="$ROOT_DIR/deploy/wsl2-acceptance-report.txt"
compose=(docker compose --env-file "$ENV_FILE" -f "$ROOT_DIR/deploy/docker-compose.yml")
{
  printf 'GeoChem WSL2 integration report\n'
  printf 'Generated: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'Application: https://%s\n' "$GEOCHEM_SITE_HOST"
  printf 'Identity: https://%s\n' "$GEOCHEM_AUTH_HOST"
  printf 'Initial administrator: %s\n\n' "${GEOCHEM_INITIAL_ADMIN_USERNAME:-geochem-admin}"
  "${compose[@]}" ps
} >"$report"

windows_script="$(wslpath -w "$ROOT_DIR/deploy/windows/install-geochem-lan.ps1")"
windows_cert="$(wslpath -w "$ROOT_DIR/deploy/geochem-lan-root.crt")"
echo
echo "WSL2 infrastructure integration passed. Report: $report"
echo "Open an Administrator PowerShell and run:"
printf 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%s" -CertificatePath "%s" -AppHost "%s" -AuthHost "%s"\n' \
  "$windows_script" "$windows_cert" "$GEOCHEM_SITE_HOST" "$GEOCHEM_AUTH_HOST"
echo
echo "Then browse to https://${GEOCHEM_SITE_HOST} and sign in as ${GEOCHEM_INITIAL_ADMIN_USERNAME:-geochem-admin}."
echo "Read its one-time password from the protected WSL file: $ENV_FILE"
echo "Choose the administrator portal and wait for its overview, then run:"
echo "bash scripts/wsl2-login-acceptance.sh"
