#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${GEOCHEM_ENV_FILE:-$ROOT_DIR/deploy/.env}"
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

source_file="$CADDY_DATA_PATH/caddy/pki/authorities/local/root.crt"
target_file="$ROOT_DIR/deploy/geochem-lan-root.crt"
if [[ ! -f "$source_file" ]]; then
  echo "Caddy local CA is not available yet. Start the stack first." >&2
  exit 1
fi
cp "$source_file" "$target_file"
chmod 644 "$target_file"
echo "$target_file"

