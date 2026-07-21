#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${GEOCHEM_ENV_FILE:-$ROOT_DIR/deploy/.env}"
COMPOSE_FILE="$ROOT_DIR/deploy/docker-compose.yml"
TIMEOUT_SECONDS="${GEOCHEM_VERIFY_TIMEOUT_SECONDS:-360}"

usage() {
  cat <<'EOF'
Usage: scripts/server-verify.sh

Verifies the running GeoChem LAN stack without mutating project data. Set
GEOCHEM_TEST_ACCESS_TOKEN to additionally verify authenticated API reads.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
if [[ $# -gt 0 ]]; then
  echo "Unknown argument: $1" >&2
  usage >&2
  exit 2
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing deployment environment: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
failures=0
warnings=0
passes=0

pass() { printf 'PASS  %s\n' "$1"; passes=$((passes + 1)); }
warn() { printf 'WARN  %s\n' "$1" >&2; warnings=$((warnings + 1)); }
fail() { printf 'FAIL  %s\n' "$1" >&2; failures=$((failures + 1)); }

wait_for() {
  local description="$1"
  shift
  local deadline=$((SECONDS + TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    if "$@" >/dev/null 2>&1; then
      return 0
    fi
    sleep 3
  done
  fail "$description did not become ready within ${TIMEOUT_SECONDS}s"
  return 1
}

container_running() {
  local service="$1"
  local container_id
  container_id="$("${compose[@]}" ps -q "$service" 2>/dev/null)"
  [[ -n "$container_id" ]] || return 1
  [[ "$(docker inspect -f '{{.State.Status}}' "$container_id" 2>/dev/null)" == "running" ]]
}

container_healthy() {
  local service="$1"
  local container_id
  container_id="$("${compose[@]}" ps -q "$service" 2>/dev/null)"
  [[ -n "$container_id" ]] || return 1
  local readiness
  readiness="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" 2>/dev/null)"
  [[ "$readiness" == "healthy" || "$readiness" == "running" ]]
}

printf 'GeoChem post-deploy verification\n'
printf 'Compose file: %s\n\n' "$COMPOSE_FILE"

if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  fail "Docker daemon is not reachable"
  printf '\nVerification result: %d pass(es), %d failure(s), %d warning(s).\n' "$passes" "$failures" "$warnings"
  exit 1
fi

for service in postgres redis keycloak web caddy; do
  if wait_for "$service container" container_healthy "$service"; then
    pass "$service container is healthy"
  fi
done
if wait_for "worker container" container_running worker; then
  pass "worker container is running"
fi

for service in postgres redis keycloak web worker caddy; do
  container_id="$("${compose[@]}" ps -q "$service" 2>/dev/null || true)"
  if [[ -z "$container_id" ]]; then
    fail "$service container is unavailable for runtime policy checks"
    continue
  fi
  restart_policy="$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$container_id" 2>/dev/null || true)"
  if [[ "$restart_policy" == "unless-stopped" ]]; then
    pass "$service uses the unless-stopped restart policy"
  else
    fail "$service restart policy is ${restart_policy:-<unset>}"
  fi
  log_driver="$(docker inspect -f '{{.HostConfig.LogConfig.Type}}' "$container_id" 2>/dev/null || true)"
  log_max_size="$(docker inspect -f '{{index .HostConfig.LogConfig.Config "max-size"}}' "$container_id" 2>/dev/null || true)"
  log_max_files="$(docker inspect -f '{{index .HostConfig.LogConfig.Config "max-file"}}' "$container_id" 2>/dev/null || true)"
  if [[ "$log_driver" == "json-file" && "$log_max_size" == "${GEOCHEM_LOG_MAX_SIZE:-20m}" && "$log_max_files" == "${GEOCHEM_LOG_MAX_FILES:-5}" ]]; then
    pass "$service log rotation is ${log_max_files} x ${log_max_size}"
  else
    fail "$service log rotation is not the configured json-file policy"
  fi
done

for service in web worker; do
  container_id="$("${compose[@]}" ps -q "$service" 2>/dev/null || true)"
  init_enabled="$(docker inspect -f '{{.HostConfig.Init}}' "$container_id" 2>/dev/null || true)"
  if [[ "$init_enabled" == "true" ]]; then
    pass "$service has an init process for signal and child-process handling"
  else
    fail "$service does not have Docker init enabled"
  fi
done

for service in postgres redis keycloak web worker; do
  container_id="$("${compose[@]}" ps -q "$service" 2>/dev/null || true)"
  if [[ -n "$container_id" && -z "$(docker port "$container_id" 2>/dev/null)" ]]; then
    pass "$service has no host-published ports"
  else
    fail "$service unexpectedly publishes a host port"
  fi
done
caddy_id="$("${compose[@]}" ps -q caddy 2>/dev/null || true)"
if [[ -n "$caddy_id" && -n "$(docker port "$caddy_id" 2>/dev/null)" ]]; then
  pass "only Caddy exposes the LAN edge ports"
else
  fail "Caddy does not expose the configured LAN edge ports"
fi

db_name="$("${compose[@]}" exec -T postgres psql -U postgres -d geochem -Atqc 'SELECT current_database();' 2>/dev/null || true)"
if [[ "$db_name" == "geochem" ]]; then
  pass "PostgreSQL geochem database accepts queries"
else
  fail "PostgreSQL geochem database query failed"
fi
checksums="$("${compose[@]}" exec -T postgres psql -U postgres -d postgres -Atqc 'SHOW data_checksums;' 2>/dev/null || true)"
if [[ "$checksums" == "on" ]]; then
  pass "PostgreSQL data checksums are enabled"
else
  fail "PostgreSQL data checksums are not enabled"
fi

redis_ping="$("${compose[@]}" exec -T redis redis-cli -a "$REDIS_PASSWORD" --no-auth-warning ping 2>/dev/null || true)"
if [[ "$redis_ping" == "PONG" ]]; then
  pass "Redis authentication and ping succeeded"
else
  fail "Redis ping failed"
fi
redis_persistence="$("${compose[@]}" exec -T redis redis-cli -a "$REDIS_PASSWORD" --no-auth-warning CONFIG GET appendonly appendfsync 2>/dev/null | tr '\n' ' ' || true)"
if [[ "$redis_persistence" == *"appendonly yes"* && "$redis_persistence" == *"appendfsync everysec"* ]]; then
  pass "Redis AOF persistence is enabled with everysec fsync"
else
  fail "Redis persistence settings are not the expected AOF/everysec values"
fi

alembic_state="$("${compose[@]}" exec -T web alembic -c /app/alembic.ini current 2>/dev/null || true)"
if [[ "$alembic_state" == *"(head)"* ]]; then
  pass "Alembic database revision is at head"
else
  fail "Alembic database revision is not at head"
fi

checkpoint_tables="$("${compose[@]}" exec -T postgres psql -U postgres -d geochem -Atqc \
  "SELECT COUNT(*) FROM pg_class WHERE relname IN ('checkpoint_migrations','checkpoints','checkpoint_blobs','checkpoint_writes') AND relkind='r';" \
  2>/dev/null || true)"
if [[ "$checkpoint_tables" == "4" ]]; then
  pass "LangGraph PostgreSQL checkpoint schema is initialized"
else
  fail "LangGraph checkpoint schema is incomplete (${checkpoint_tables:-0}/4 tables)"
fi

worker_ping="$("${compose[@]}" exec -T worker python -c 'from geochem.background_tasks import celery_app; replies = celery_app.control.inspect(timeout=10).ping() if celery_app else None; assert replies, "no Celery worker replied"; print("ok")' 2>/dev/null || true)"
if [[ "$worker_ping" == *"ok"* ]]; then
  pass "Celery worker answered a control ping"
else
  fail "Celery worker did not answer a control ping"
fi

task_roundtrip="$("${compose[@]}" exec -T web python -c 'from uuid import uuid4; from geochem.background_tasks import celery_app; nonce=uuid4().hex; result=celery_app.send_task("geochem.health.probe", args=[nonce], queue="geochem"); payload=result.get(timeout=30); assert payload.get("status") == "ok" and payload.get("nonce") == nonce; print("ok")' 2>/dev/null || true)"
if [[ "$task_roundtrip" == "ok" ]]; then
  pass "Celery task completed a Redis broker/result-backend round trip"
else
  fail "Celery task could not complete a Redis broker/result-backend round trip"
fi

if "${compose[@]}" exec -T web sh -ec 'test -w /data/config && test -w /data/workspaces && test -w /data/exports'; then
  pass "application storage directories are writable by the Web process"
else
  fail "one or more application storage directories are not writable"
fi

if "${compose[@]}" exec -T web curl --fail --silent http://127.0.0.1:8765/api/v1/health >/dev/null; then
  pass "FastAPI internal health endpoint is ready"
else
  fail "FastAPI internal health endpoint failed"
fi
if "${compose[@]}" exec -T web curl --fail --silent http://127.0.0.1:8765/api/v1/ready >/dev/null; then
  pass "FastAPI dependency readiness endpoint is ready"
else
  fail "FastAPI dependency readiness endpoint failed"
fi
api_docs_code="$("${compose[@]}" exec -T web curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:8765/openapi.json 2>/dev/null || true)"
if [[ "$api_docs_code" == "404" ]]; then
  pass "production OpenAPI and interactive API documentation are disabled"
else
  fail "production OpenAPI endpoint returned ${api_docs_code:-<no response>} instead of 404"
fi
if "${compose[@]}" exec -T keycloak bash -ec "{ printf 'HEAD /health/ready HTTP/1.0\\r\\n\\r\\n' >&0; grep -q 'HTTP/1.0 200'; } 0<>/dev/tcp/127.0.0.1/9000"; then
  pass "Keycloak management readiness endpoint is ready"
else
  fail "Keycloak management readiness endpoint failed"
fi

user_admin_probe="$("${compose[@]}" exec -T web python -c 'from geochem.core.runtime import load_runtime_settings; from geochem.web.keycloak_admin import KeycloakAdminClient; client=KeycloakAdminClient(load_runtime_settings()); data=client.list_users(limit=200); assert any("admin" in user["roles"] for user in data["users"]), "no GeoChem admin user"; client.close(); print("ok")' 2>/dev/null || true)"
if [[ "$user_admin_probe" == "ok" ]]; then
  pass "Keycloak service account can read an initial GeoChem administrator"
else
  fail "Keycloak in-app user service or initial GeoChem administrator is unavailable"
fi

auth_config_probe="$("${compose[@]}" exec -T web python -c 'import httpx; data=httpx.get("http://127.0.0.1:8765/api/v1/auth/config", timeout=10).json(); assert data.get("enabled") is True; assert str(data.get("issuer_url", "")).startswith("https://"); print("ok")' 2>/dev/null || true)"
if [[ "$auth_config_probe" == "ok" ]]; then
  pass "Web login configuration exposes production OIDC"
else
  fail "Web login configuration is not production OIDC"
fi

ca_file="$ROOT_DIR/deploy/geochem-lan-root.crt"
if GEOCHEM_ENV_FILE="$ENV_FILE" "$ROOT_DIR/scripts/export-caddy-ca.sh" >/dev/null 2>&1 && [[ -s "$ca_file" ]]; then
  pass "Caddy local CA was exported"
else
  fail "Caddy local CA could not be exported"
fi

https_port="${GEOCHEM_HTTPS_PORT:-443}"
site_origin="https://${GEOCHEM_SITE_HOST}"
auth_origin="https://${GEOCHEM_AUTH_HOST}"
[[ "$https_port" != "443" ]] && site_origin="${site_origin}:${https_port}" && auth_origin="${auth_origin}:${https_port}"
curl_common=(--silent --show-error --cacert "$ca_file" --resolve "${GEOCHEM_SITE_HOST}:${https_port}:127.0.0.1")
if curl "${curl_common[@]}" --fail "$site_origin/api/v1/ready" >/dev/null; then
  pass "GeoChem HTTPS readiness endpoint is reachable through Caddy"
else
  fail "GeoChem HTTPS readiness endpoint failed"
fi
auth_curl=(--silent --show-error --cacert "$ca_file" --resolve "${GEOCHEM_AUTH_HOST}:${https_port}:127.0.0.1")
if curl "${auth_curl[@]}" --fail "$auth_origin/realms/geochem/.well-known/openid-configuration" >/dev/null; then
  pass "Keycloak realm is reachable through Caddy HTTPS"
else
  fail "Keycloak realm HTTPS endpoint failed"
fi

unauthenticated_code="$(curl "${curl_common[@]}" --output /dev/null --write-out '%{http_code}' "$site_origin/api/v1/articles?project_id=DEFAULT_WORKSPACE" 2>/dev/null || true)"
if [[ "$unauthenticated_code" == "401" ]]; then
  pass "protected API rejects unauthenticated requests"
else
  fail "protected API returned HTTP ${unauthenticated_code:-<none>} without a token"
fi

if [[ -n "${GEOCHEM_TEST_ACCESS_TOKEN:-}" ]]; then
  authenticated_code="$(curl "${curl_common[@]}" --header "Authorization: Bearer $GEOCHEM_TEST_ACCESS_TOKEN" --output /dev/null --write-out '%{http_code}' "$site_origin/api/v1/auth/me" 2>/dev/null || true)"
  if [[ "$authenticated_code" == "200" ]]; then
    pass "temporary access token can read the authenticated API"
  else
    fail "temporary access token returned HTTP ${authenticated_code:-<none>}"
  fi
  article_read_code="$(curl "${curl_common[@]}" --header "Authorization: Bearer $GEOCHEM_TEST_ACCESS_TOKEN" --output /dev/null --write-out '%{http_code}' "$site_origin/api/v1/articles?project_id=DEFAULT_WORKSPACE" 2>/dev/null || true)"
  if [[ "$article_read_code" == "200" ]]; then
    pass "temporary access token can read shared-workspace article data"
  else
    fail "shared-workspace article read returned HTTP ${article_read_code:-<none>}"
  fi
else
  warn "GEOCHEM_TEST_ACCESS_TOKEN is unset; after browser login run scripts/wsl2-login-acceptance.sh to verify the administrator portal"
fi

printf '\nVerification result: %d pass(es), %d failure(s), %d warning(s).\n' "$passes" "$failures" "$warnings"
(( failures == 0 ))
