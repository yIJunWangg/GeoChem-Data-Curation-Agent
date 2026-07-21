#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${GEOCHEM_ENV_FILE:-$ROOT_DIR/deploy/.env}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_DIR="${GEOCHEM_ACCEPTANCE_REPORT_DIR:-$ROOT_DIR/deploy/acceptance/$TIMESTAMP}"
CURRENT_STEP="initialization"
ACCEPTANCE_STATUS="failed"
BACKUP_STATUS="required"

mkdir -p "$REPORT_DIR"
chmod 700 "$REPORT_DIR"

write_summary() {
  local exit_code="$1"
  local finished_at
  local revision
  finished_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  revision="$(git -C "$ROOT_DIR" rev-parse --short=12 HEAD 2>/dev/null || printf unknown)"
  if command -v docker >/dev/null 2>&1 && [[ -f "$ENV_FILE" ]]; then
    docker compose --env-file "$ENV_FILE" -f "$ROOT_DIR/deploy/docker-compose.yml" ps \
      >"$REPORT_DIR/compose-services.txt" 2>&1 || true
  fi
  cat >"$REPORT_DIR/summary.md" <<EOF
# GeoChem production acceptance

- Status: **$ACCEPTANCE_STATUS**
- Exit code: $exit_code
- Started: $TIMESTAMP
- Finished: $finished_at
- Host: $(hostname)
- Revision: $revision
- Last step: $CURRENT_STEP
- Backup: $BACKUP_STATUS
- Load users: ${GEOCHEM_LOAD_USERS:-50}
- Load duration: ${GEOCHEM_LOAD_DURATION:-120}s

The step logs and capacity JSON in this directory are the authoritative
acceptance evidence. No API keys, passwords or access tokens are written here.
EOF
  ACCEPTANCE_STATUS_VALUE="$ACCEPTANCE_STATUS" \
  ACCEPTANCE_EXIT_CODE="$exit_code" \
  ACCEPTANCE_STARTED="$TIMESTAMP" \
  ACCEPTANCE_FINISHED="$finished_at" \
  ACCEPTANCE_HOST="$(hostname)" \
  ACCEPTANCE_REVISION="$revision" \
  ACCEPTANCE_LAST_STEP="$CURRENT_STEP" \
  ACCEPTANCE_BACKUP="$BACKUP_STATUS" \
  ACCEPTANCE_REPORT_PATH="$REPORT_DIR/summary.json" \
    python3 -c 'import json, os; from pathlib import Path; data={"status":os.environ["ACCEPTANCE_STATUS_VALUE"],"exit_code":int(os.environ["ACCEPTANCE_EXIT_CODE"]),"started":os.environ["ACCEPTANCE_STARTED"],"finished":os.environ["ACCEPTANCE_FINISHED"],"host":os.environ["ACCEPTANCE_HOST"],"revision":os.environ["ACCEPTANCE_REVISION"],"last_step":os.environ["ACCEPTANCE_LAST_STEP"],"backup":os.environ["ACCEPTANCE_BACKUP"],"capacity_report":"capacity-report.json"}; Path(os.environ["ACCEPTANCE_REPORT_PATH"]).write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")' \
    >/dev/null 2>&1 || true
  printf '\nAcceptance report: %s\n' "$REPORT_DIR/summary.md"
}

on_exit() {
  local exit_code="$?"
  write_summary "$exit_code"
}
trap on_exit EXIT

run_step() {
  local name="$1"
  local log_name="$2"
  shift 2
  CURRENT_STEP="$name"
  printf '\n=== %s ===\n' "$name"
  "$@" 2>&1 | tee "$REPORT_DIR/$log_name"
}

if [[ -z "${GEOCHEM_TEST_ACCESS_TOKEN:-}" ]]; then
  echo "GEOCHEM_TEST_ACCESS_TOKEN is required for production business-read capacity acceptance." >&2
  echo "Use a temporary viewer token and unset it immediately after this command." >&2
  exit 2
fi

if ! command -v systemctl >/dev/null 2>&1 || [[ ! -d /run/systemd/system ]]; then
  echo "A running systemd host is required for production acceptance." >&2
  exit 2
fi
for unit in geochem.service geochem-backup.timer; do
  if ! systemctl is-enabled --quiet "$unit"; then
    echo "$unit is not enabled. Run scripts/install-server-services.sh --start first." >&2
    exit 2
  fi
  if ! systemctl is-active --quiet "$unit"; then
    echo "$unit is not active. Start it before production acceptance." >&2
    exit 2
  fi
done

run_step "production preflight" "01-preflight.log" \
  env GEOCHEM_ENV_FILE="$ENV_FILE" "$ROOT_DIR/scripts/server-preflight.sh" --production --require-backup
run_step "runtime verification" "02-runtime-verify.log" \
  env GEOCHEM_ENV_FILE="$ENV_FILE" "$ROOT_DIR/scripts/server-verify.sh"
if [[ "${GEOCHEM_ACCEPTANCE_SKIP_BACKUP:-false}" != "true" ]]; then
  echo "Running a complete PostgreSQL + restic backup as part of production acceptance..."
  run_step "PostgreSQL and NAS backup" "03-backup.log" \
    env GEOCHEM_ENV_FILE="$ENV_FILE" "$ROOT_DIR/scripts/backup-server.sh"
  BACKUP_STATUS="passed"
else
  BACKUP_STATUS="skipped"
  echo "WARNING: backup execution was explicitly skipped; production backup acceptance remains incomplete." >&2
fi
run_step "authenticated capacity baseline" "04-capacity.log" \
  env GEOCHEM_ENV_FILE="$ENV_FILE" \
    GEOCHEM_LOAD_USERS="${GEOCHEM_LOAD_USERS:-50}" \
    GEOCHEM_LOAD_DURATION="${GEOCHEM_LOAD_DURATION:-120}" \
    GEOCHEM_LOAD_REPORT="$REPORT_DIR/capacity-report.json" \
    "$ROOT_DIR/scripts/server-capacity-test.sh"

if [[ "$BACKUP_STATUS" == "skipped" ]]; then
  ACCEPTANCE_STATUS="partial"
  CURRENT_STEP="backup acceptance incomplete"
  echo "GeoChem runtime and capacity checks passed, but backup acceptance was skipped." >&2
  exit 3
fi

ACCEPTANCE_STATUS="passed"
CURRENT_STEP="complete"
echo "GeoChem production acceptance checks passed."
