#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="${1:-${ROOT}/geochem-data/DEFAULT_WORKSPACE/geochem.db}"

if [[ -z "${GEOCHEM_DATABASE_URL:-}" ]]; then
  echo "GEOCHEM_DATABASE_URL is required." >&2
  exit 2
fi

exec "${ROOT}/.venv/bin/geochem-migrate-postgres" \
  "${SOURCE}" \
  --database-url "${GEOCHEM_DATABASE_URL}"
