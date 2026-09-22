#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${GEOCHEM_ENV_FILE:-$ROOT_DIR/deploy/.env}"
COMPOSE_FILE="$ROOT_DIR/deploy/docker-compose.yml"
CREDENTIAL_MASTER_KEY_FILE="$ROOT_DIR/deploy/secrets/credential_master_key"
MODE="staging"
REQUIRE_BACKUP="false"

usage() {
  cat <<'EOF'
Usage: scripts/server-preflight.sh [--production] [--require-backup]

Checks the Linux host, deployment secrets, storage, Docker and Compose before
GeoChem is started. Use --production on the Ubuntu server. --require-backup
also requires a mounted NAS and a readable restic password file.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --production) MODE="production" ;;
    --require-backup) REQUIRE_BACKUP="true" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

failures=0
warnings=0

pass() { printf 'PASS  %s\n' "$1"; }
warn() { printf 'WARN  %s\n' "$1" >&2; warnings=$((warnings + 1)); }
fail() { printf 'FAIL  %s\n' "$1" >&2; failures=$((failures + 1)); }

check_command() {
  if command -v "$1" >/dev/null 2>&1; then
    pass "command available: $1"
  else
    fail "missing required command: $1"
  fi
}

check_integer_range() {
  local name="$1"
  local value="$2"
  local minimum="$3"
  local maximum="$4"
  if [[ "$value" =~ ^[0-9]+$ ]] && (( value >= minimum && value <= maximum )); then
    pass "$name=$value"
  else
    fail "$name must be an integer between $minimum and $maximum"
  fi
}

check_log_size() {
  local value="$1"
  if [[ "$value" =~ ^[1-9][0-9]*[kKmMgG]$ ]]; then
    pass "GEOCHEM_LOG_MAX_SIZE=$value"
  else
    fail "GEOCHEM_LOG_MAX_SIZE must use Docker size syntax such as 20m or 1g"
  fi
}

printf 'GeoChem host preflight (%s)\n' "$MODE"
printf 'Repository: %s\n\n' "$ROOT_DIR"

if [[ "$(uname -s)" == "Linux" ]]; then
  pass "Linux host detected"
else
  fail "server deployment requires Linux; use scripts/mac-preview.sh on macOS"
fi

architecture="$(uname -m)"
case "$architecture" in
  x86_64|amd64) pass "supported server architecture: $architecture" ;;
  aarch64|arm64) warn "ARM64 is usable but the planned WSL2/Ubuntu acceptance target is x86_64" ;;
  *) fail "unsupported architecture: $architecture" ;;
esac

if grep -qi microsoft /proc/version 2>/dev/null; then
  pass "WSL2 host detected"
  if [[ "$ROOT_DIR" == /mnt/* ]]; then
    fail "repository is under /mnt; move it into the WSL2 Linux filesystem"
  else
    pass "repository is stored in the WSL2 Linux filesystem"
  fi
fi

for command_name in docker openssl curl python3 awk sed grep df ss; do
  check_command "$command_name"
done

if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    pass "Docker daemon is reachable"
  else
    fail "Docker daemon is not reachable"
  fi
  if docker compose version >/dev/null 2>&1; then
    compose_version="$(docker compose version --short 2>/dev/null | sed 's/^v//')"
    minimum_compose_version="2.20.0"
    if [[ -n "$compose_version" && "$(printf '%s\n%s\n' "$minimum_compose_version" "$compose_version" | sort -V | head -n1)" == "$minimum_compose_version" ]]; then
      pass "Docker Compose $compose_version is supported"
    else
      fail "Docker Compose ${compose_version:-<unknown>} is too old; version $minimum_compose_version or newer is required"
    fi
  else
    fail "Docker Compose v2 is required"
  fi
fi

memory_kib="$(awk '/MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
memory_gib=$((memory_kib / 1024 / 1024))
minimum_memory=12
[[ "$MODE" == "production" ]] && minimum_memory=24
if (( memory_gib >= minimum_memory )); then
  pass "memory: ${memory_gib} GiB"
else
  fail "memory: ${memory_gib} GiB; ${MODE} requires at least ${minimum_memory} GiB"
fi

cpu_count="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"
if (( cpu_count >= 4 )); then
  pass "CPU threads: $cpu_count"
else
  warn "only $cpu_count CPU threads are available; Docling tasks may queue slowly"
fi

disk_kib="$(df -Pk "$ROOT_DIR" | awk 'NR==2 {print $4}')"
disk_gib=$((disk_kib / 1024 / 1024))
minimum_disk=40
[[ "$MODE" == "production" ]] && minimum_disk=80
if (( disk_gib >= minimum_disk )); then
  pass "free disk near repository: ${disk_gib} GiB"
else
  fail "free disk: ${disk_gib} GiB; keep at least ${minimum_disk} GiB free"
fi

if [[ ! -f "$ENV_FILE" ]]; then
  fail "deployment environment is missing: $ENV_FILE (run scripts/server-bootstrap.sh)"
else
  pass "deployment environment exists"
  env_mode="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || echo unknown)"
  if [[ "$env_mode" == "600" || "$env_mode" == "400" ]]; then
    pass "deployment environment permissions: $env_mode"
  else
    fail "deployment environment must be mode 600 or 400, found $env_mode"
  fi

  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  QDRANT_DATA_PATH="${QDRANT_DATA_PATH:-/srv/geochem/qdrant}"

  secret_names=(
    POSTGRES_SUPERUSER_PASSWORD GEOCHEM_DB_PASSWORD KEYCLOAK_DB_PASSWORD
    REDIS_PASSWORD KEYCLOAK_ADMIN_PASSWORD KEYCLOAK_ADMIN_API_CLIENT_SECRET
    GEOCHEM_INITIAL_ADMIN_PASSWORD
  )
  secret_values=()
  for secret_name in "${secret_names[@]}"; do
    secret_value="${!secret_name:-}"
    if [[ -z "$secret_value" || "$secret_value" == replace-with-* ]]; then
      fail "$secret_name is not configured"
    elif (( ${#secret_value} < 24 )); then
      fail "$secret_name must contain at least 24 characters"
    elif [[ "$secret_value" =~ [:/@[:space:]] ]]; then
      fail "$secret_name must be URL-safe (no colon, slash, at-sign or whitespace)"
    else
      pass "$secret_name is configured"
      secret_values+=("$secret_value")
    fi
  done

  if [[ "${GEOCHEM_INITIAL_ADMIN_USERNAME:-}" =~ ^[A-Za-z0-9._-]{3,64}$ ]]; then
    pass "initial GeoChem administrator username is valid"
  else
    fail "GEOCHEM_INITIAL_ADMIN_USERNAME must contain 3-64 letters, numbers, dots, underscores or hyphens"
  fi
  if (( ${#secret_values[@]} > 1 )); then
    unique_count="$(printf '%s\n' "${secret_values[@]}" | sort -u | wc -l | tr -d ' ')"
    if (( unique_count == ${#secret_values[@]} )); then
      pass "infrastructure passwords are distinct"
    else
      fail "infrastructure services must not share passwords"
    fi
  fi

  for path_name in GEOCHEM_DATA_PATH POSTGRES_DATA_PATH REDIS_DATA_PATH QDRANT_DATA_PATH CADDY_DATA_PATH CADDY_CONFIG_PATH; do
    path_value="${!path_name:-}"
    if [[ "$path_value" == /* ]]; then
      pass "$path_name uses an absolute path"
    else
      fail "$path_name must be an absolute Linux path"
    fi
  done

  if [[ -n "${GEOCHEM_SITE_HOST:-}" && -n "${GEOCHEM_AUTH_HOST:-}" && "$GEOCHEM_SITE_HOST" != "$GEOCHEM_AUTH_HOST" ]]; then
    pass "application and identity hosts are distinct"
  else
    fail "GEOCHEM_SITE_HOST and GEOCHEM_AUTH_HOST must be distinct"
  fi
  if [[ "${GEOCHEM_HTTP_PORT:-80}" == "80" && "${GEOCHEM_HTTPS_PORT:-443}" == "443" ]]; then
    pass "standard LAN HTTP/HTTPS ports are configured"
  else
    fail "the OIDC LAN profile currently requires GEOCHEM_HTTP_PORT=80 and GEOCHEM_HTTPS_PORT=443"
  fi

  check_integer_range GEOCHEM_WEB_WORKERS "${GEOCHEM_WEB_WORKERS:-2}" 1 8
  check_integer_range GEOCHEM_WORKER_CONCURRENCY "${GEOCHEM_WORKER_CONCURRENCY:-2}" 1 8
  check_integer_range GEOCHEM_REQUEST_RATE_PER_MINUTE "${GEOCHEM_REQUEST_RATE_PER_MINUTE:-300}" 10 10000
  check_integer_range GEOCHEM_CHAT_RATE_PER_MINUTE "${GEOCHEM_CHAT_RATE_PER_MINUTE:-40}" 1 1000
  check_integer_range GEOCHEM_UPLOAD_RATE_PER_HOUR "${GEOCHEM_UPLOAD_RATE_PER_HOUR:-20}" 1 1000
  check_integer_range GEOCHEM_MAX_CONCURRENT_TASKS_PER_USER "${GEOCHEM_MAX_CONCURRENT_TASKS_PER_USER:-3}" 1 50
  check_integer_range GEOCHEM_DB_POOL_SIZE "${GEOCHEM_DB_POOL_SIZE:-5}" 1 20
  check_integer_range GEOCHEM_DB_MAX_OVERFLOW "${GEOCHEM_DB_MAX_OVERFLOW:-5}" 0 20
  check_integer_range GEOCHEM_DB_POOL_TIMEOUT "${GEOCHEM_DB_POOL_TIMEOUT:-30}" 5 120
  check_log_size "${GEOCHEM_LOG_MAX_SIZE:-20m}"
  check_integer_range GEOCHEM_LOG_MAX_FILES "${GEOCHEM_LOG_MAX_FILES:-5}" 1 20
  if [[ "${GEOCHEM_FORWARDED_ALLOW_IPS:-}" == "*" ]]; then
    pass "FastAPI trusts forwarded headers from the private Compose network"
  else
    fail "GEOCHEM_FORWARDED_ALLOW_IPS must be '*' while only Caddy can reach the Web container"
  fi
  if [[ "${GEOCHEM_ENABLE_API_DOCS:-false}" == "false" ]]; then
    pass "production API documentation is disabled"
  else
    fail "GEOCHEM_ENABLE_API_DOCS must be false for the LAN production stack"
  fi
  check_integer_range GEOCHEM_TASK_TIME_LIMIT_SECONDS "${GEOCHEM_TASK_TIME_LIMIT_SECONDS:-7200}" 600 43200
  check_integer_range GEOCHEM_TASK_SOFT_TIME_LIMIT_SECONDS "${GEOCHEM_TASK_SOFT_TIME_LIMIT_SECONDS:-6900}" 300 43140
  check_integer_range GEOCHEM_TASK_VISIBILITY_TIMEOUT_SECONDS "${GEOCHEM_TASK_VISIBILITY_TIMEOUT_SECONDS:-7500}" 900 86400
  check_integer_range GEOCHEM_TASK_RESULT_EXPIRES_SECONDS "${GEOCHEM_TASK_RESULT_EXPIRES_SECONDS:-86400}" 3600 604800
  if (( ${GEOCHEM_TASK_SOFT_TIME_LIMIT_SECONDS:-6900} < ${GEOCHEM_TASK_TIME_LIMIT_SECONDS:-7200} )); then
    pass "Celery soft task limit is below the hard limit"
  else
    fail "GEOCHEM_TASK_SOFT_TIME_LIMIT_SECONDS must be below GEOCHEM_TASK_TIME_LIMIT_SECONDS"
  fi
  if (( ${GEOCHEM_TASK_VISIBILITY_TIMEOUT_SECONDS:-7500} > ${GEOCHEM_TASK_TIME_LIMIT_SECONDS:-7200} )); then
    pass "Redis visibility timeout exceeds the Celery hard task limit"
  else
    fail "GEOCHEM_TASK_VISIBILITY_TIMEOUT_SECONDS must exceed GEOCHEM_TASK_TIME_LIMIT_SECONDS"
  fi

  llm_key_count=0
  for key_name in OPENCODE_GO_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY DEEPSEEK_API_KEY MIMO_API_KEY OPENROUTER_API_KEY; do
    [[ -n "${!key_name:-}" ]] && llm_key_count=$((llm_key_count + 1))
  done
  if (( llm_key_count > 0 )); then
    pass "$llm_key_count shared LLM provider key(s) configured"
  else
    warn "no environment-based LLM provider key is configured; add an encrypted credential in the GeoChem admin console after startup"
  fi

  if [[ "$REQUIRE_BACKUP" == "true" ]]; then
    check_command restic
    check_command mountpoint
    if command -v mountpoint >/dev/null 2>&1 && mountpoint -q "${GEOCHEM_NAS_MOUNT:-}"; then
      pass "NAS is mounted at $GEOCHEM_NAS_MOUNT"
    else
      fail "NAS is not mounted at ${GEOCHEM_NAS_MOUNT:-<unset>}"
    fi
    if [[ -r "${RESTIC_PASSWORD_FILE:-}" ]]; then
      pass "restic password file is readable"
    else
      fail "restic password file is not readable: ${RESTIC_PASSWORD_FILE:-<unset>}"
    fi
  fi
fi

if [[ ! -f "$CREDENTIAL_MASTER_KEY_FILE" ]]; then
  fail "model credential master key is missing: $CREDENTIAL_MASTER_KEY_FILE"
else
  credential_key_mode="$(stat -c '%a' "$CREDENTIAL_MASTER_KEY_FILE" 2>/dev/null || echo unknown)"
  credential_key_value="$(tr -d '\r\n' < "$CREDENTIAL_MASTER_KEY_FILE")"
  if [[ "$credential_key_mode" == "600" || "$credential_key_mode" == "400" ]]; then
    pass "model credential master key permissions: $credential_key_mode"
  else
    fail "model credential master key must be mode 600 or 400, found $credential_key_mode"
  fi
  if [[ "$credential_key_value" =~ ^[0-9a-fA-F]{64}$ ]]; then
    pass "model credential master key is a valid 256-bit hexadecimal key"
  else
    fail "model credential master key must contain exactly 64 hexadecimal characters"
  fi
fi

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 && [[ -f "$ENV_FILE" ]]; then
  if docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config --quiet; then
    pass "Docker Compose configuration renders successfully"
  else
    fail "Docker Compose configuration is invalid"
  fi

  compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
  caddy_id="$("${compose[@]}" ps -q caddy 2>/dev/null || true)"
  for port in "${GEOCHEM_HTTP_PORT:-80}" "${GEOCHEM_HTTPS_PORT:-443}"; do
    if ss -H -ltn "sport = :$port" 2>/dev/null | grep -q .; then
      if [[ -n "$caddy_id" ]]; then
        pass "TCP port $port is owned by the existing GeoChem stack"
      else
        fail "TCP port $port is already in use"
      fi
    else
      pass "TCP port $port is available"
    fi
  done
fi

printf '\nPreflight result: %d failure(s), %d warning(s).\n' "$failures" "$warnings"
(( failures == 0 ))
