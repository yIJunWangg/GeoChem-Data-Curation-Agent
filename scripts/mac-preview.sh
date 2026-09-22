#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -x ".venv/bin/geochem-web" ]]; then
  echo "GeoChem virtual environment is missing. Run: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
  exit 1
fi

preview_build_id="$({
  .venv/bin/python -c 'from hashlib import sha256; from pathlib import Path; root=Path("."); paths=[root/"pyproject.toml", *sorted((root/"src"/"geochem").rglob("*.py")), *sorted((root/"web"/"src").rglob("*"))]; digest=sha256(); [(digest.update(str(path.relative_to(root)).encode()), digest.update(b"\0"), digest.update(path.read_bytes()), digest.update(b"\0")) for path in paths if path.is_file()]; print(digest.hexdigest()[:12])'
} 2>/dev/null)"
export GEOCHEM_BUILD_ID="${GEOCHEM_BUILD_ID:-$preview_build_id}"

if [[ "${GEOCHEM_SKIP_WEB_BUILD:-false}" != "true" ]]; then
  echo "Building the React preview..."
  npm --prefix web run build
fi

export GEOCHEM_PROFILE=development
export GEOCHEM_HOST="${GEOCHEM_HOST:-127.0.0.1}"
export GEOCHEM_PORT="${GEOCHEM_PORT:-8765}"
export GEOCHEM_OPEN_BROWSER="${GEOCHEM_OPEN_BROWSER:-true}"
export GEOCHEM_VECTOR_ENABLED="${GEOCHEM_VECTOR_ENABLED:-true}"

preview_url="http://127.0.0.1:${GEOCHEM_PORT}"
health_payload="$(curl --fail --silent --max-time 2 "$preview_url/api/v1/health" 2>/dev/null || true)"
running_build_id="$(.venv/bin/python -c 'import json,sys
try: print(json.loads(sys.argv[1]).get("build_id", ""))
except Exception: print("")' "$health_payload")"
if [[ -n "$health_payload" && "$running_build_id" == "$GEOCHEM_BUILD_ID" ]]; then
  echo "GeoChem preview is already running with the current build: $preview_url"
  echo "Build: $GEOCHEM_BUILD_ID"
  if [[ "$GEOCHEM_OPEN_BROWSER" == "true" ]] && command -v open >/dev/null 2>&1; then
    open "$preview_url"
  fi
  exit 0
elif [[ -n "$health_payload" ]]; then
  echo "GeoChem on $preview_url is an older process (build ${running_build_id:-unknown})."
  echo "Starting the current build $GEOCHEM_BUILD_ID on a free preview port instead."
fi

port_in_use() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
  else
    nc -z 127.0.0.1 "$1" >/dev/null 2>&1
  fi
}

if port_in_use "$GEOCHEM_PORT"; then
  requested_port="$GEOCHEM_PORT"
  for candidate in $(seq $((requested_port + 1)) $((requested_port + 50))); do
    if ! port_in_use "$candidate"; then
      export GEOCHEM_PORT="$candidate"
      echo "Port $requested_port is occupied by another service; using $candidate instead."
      break
    fi
  done
  if [[ "$GEOCHEM_PORT" == "$requested_port" ]]; then
    echo "No free preview port was found after $requested_port." >&2
    exit 1
  fi
fi

echo "GeoChem Mac preview"
echo "  URL:   http://127.0.0.1:${GEOCHEM_PORT}"
echo "  Build: $GEOCHEM_BUILD_ID"
echo "  Mode:  development (SQLite, local tasks, login disabled)"
exec .venv/bin/geochem-web "$@"
