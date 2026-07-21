#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${GEOCHEM_ENV_FILE:-$ROOT_DIR/deploy/.env}"
USERS="${GEOCHEM_LOAD_USERS:-20}"
DURATION="${GEOCHEM_LOAD_DURATION:-60}"
OUTPUT="${GEOCHEM_LOAD_REPORT:-$ROOT_DIR/deploy/capacity-report.json}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing deployment environment: $ENV_FILE" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

https_port="${GEOCHEM_HTTPS_PORT:-443}"
base_url="https://${GEOCHEM_SITE_HOST}"
[[ "$https_port" != "443" ]] && base_url="${base_url}:${https_port}"

args=(
  --base-url "$base_url"
  --users "$USERS"
  --duration "$DURATION"
  --ca-cert "$ROOT_DIR/deploy/geochem-lan-root.crt"
  --output "$OUTPUT"
)
[[ -n "${GEOCHEM_TEST_ACCESS_TOKEN:-}" ]] && args+=(--token "$GEOCHEM_TEST_ACCESS_TOKEN")

python3 "$ROOT_DIR/scripts/load-test.py" "${args[@]}"

