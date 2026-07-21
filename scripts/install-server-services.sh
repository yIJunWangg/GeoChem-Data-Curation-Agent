#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${GEOCHEM_ENV_FILE:-$ROOT_DIR/deploy/.env}"
COMPOSE_FILE="$ROOT_DIR/deploy/docker-compose.yml"
OUTPUT_DIR=""
START_SERVICES="false"

usage() {
  cat <<'EOF'
Usage: scripts/install-server-services.sh [options]

Options:
  --render-only DIR  Render systemd units into DIR without installing them.
  --start            Start GeoChem and the backup timer after installation.
  -h, --help         Show this help.

Run the install mode with sudo on the Ubuntu production server. The render-only
mode is portable and is used to validate substitutions before Linux deployment.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --render-only)
      [[ $# -ge 2 ]] || { echo "--render-only requires a directory" >&2; exit 2; }
      OUTPUT_DIR="$2"
      shift
      ;;
    --start) START_SERVICES="true" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [[ "$ENV_FILE" != /* ]]; then
  ENV_FILE="$ROOT_DIR/$ENV_FILE"
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing deployment environment: $ENV_FILE" >&2
  echo "Run scripts/server-bootstrap.sh on the Linux host first." >&2
  exit 1
fi
ENV_FILE="$(cd "$(dirname "$ENV_FILE")" && pwd)/$(basename "$ENV_FILE")"

DOCKER_BIN="$(command -v docker 2>/dev/null || true)"
if [[ -z "$DOCKER_BIN" ]]; then
  if [[ -n "$OUTPUT_DIR" ]]; then
    DOCKER_BIN="/usr/bin/docker"
  else
    echo "Docker CLI is required before installing the GeoChem service." >&2
    exit 1
  fi
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

if [[ -z "${GEOCHEM_NAS_MOUNT:-}" || "$GEOCHEM_NAS_MOUNT" != /* ]]; then
  echo "GEOCHEM_NAS_MOUNT must be an absolute path." >&2
  exit 1
fi

escape_sed_replacement() {
  printf '%s' "$1" | sed 's/[&|]/\\&/g'
}

render_unit() {
  local source="$1"
  local destination="$2"
  local root env_file compose_file nas_mount docker_bin
  root="$(escape_sed_replacement "$ROOT_DIR")"
  env_file="$(escape_sed_replacement "$ENV_FILE")"
  compose_file="$(escape_sed_replacement "$COMPOSE_FILE")"
  nas_mount="$(escape_sed_replacement "$GEOCHEM_NAS_MOUNT")"
  docker_bin="$(escape_sed_replacement "$DOCKER_BIN")"
  sed \
    -e "s|@GEOCHEM_ROOT@|$root|g" \
    -e "s|@GEOCHEM_ENV_FILE@|$env_file|g" \
    -e "s|@GEOCHEM_COMPOSE_FILE@|$compose_file|g" \
    -e "s|@GEOCHEM_NAS_MOUNT@|$nas_mount|g" \
    -e "s|@DOCKER_BIN@|$docker_bin|g" \
    "$source" > "$destination"
}

if [[ -n "$OUTPUT_DIR" ]]; then
  mkdir -p "$OUTPUT_DIR"
  target_dir="$(cd "$OUTPUT_DIR" && pwd)"
else
  if [[ "$(uname -s)" != "Linux" ]]; then
    echo "Systemd installation requires Linux; use --render-only on macOS." >&2
    exit 1
  fi
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "Install mode requires root. Run: sudo bash scripts/install-server-services.sh" >&2
    exit 1
  fi
  target_dir="$(mktemp -d)"
  trap 'rm -rf "$target_dir"' EXIT
fi

render_unit "$ROOT_DIR/deploy/systemd/geochem.service.in" "$target_dir/geochem.service"
render_unit "$ROOT_DIR/deploy/systemd/geochem-backup.service.in" "$target_dir/geochem-backup.service"
cp "$ROOT_DIR/deploy/systemd/geochem-backup.timer" "$target_dir/geochem-backup.timer"

for unit in geochem.service geochem-backup.service geochem-backup.timer; do
  if grep -Eq '@(GEOCHEM_|DOCKER_)' "$target_dir/$unit"; then
    echo "Unresolved template variable in $unit" >&2
    exit 1
  fi
done

if [[ -z "$OUTPUT_DIR" ]] && command -v systemd-analyze >/dev/null 2>&1; then
  systemd-analyze verify \
    "$target_dir/geochem.service" \
    "$target_dir/geochem-backup.service" \
    "$target_dir/geochem-backup.timer"
fi

if [[ -n "$OUTPUT_DIR" ]]; then
  echo "Rendered GeoChem systemd units in $target_dir"
  exit 0
fi

install -m 0644 "$target_dir/geochem.service" /etc/systemd/system/geochem.service
install -m 0644 "$target_dir/geochem-backup.service" /etc/systemd/system/geochem-backup.service
install -m 0644 "$target_dir/geochem-backup.timer" /etc/systemd/system/geochem-backup.timer
systemctl daemon-reload
systemctl enable geochem.service geochem-backup.timer

if [[ "$START_SERVICES" == "true" ]]; then
  systemctl start geochem.service
  systemctl start geochem-backup.timer
fi

echo "Installed and enabled geochem.service and geochem-backup.timer."
echo "Start now with: systemctl start geochem.service geochem-backup.timer"
