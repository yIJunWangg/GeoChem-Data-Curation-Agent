#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${GEOCHEM_ENV_FILE:-$ROOT_DIR/deploy/.env}"
WINDOW_MINUTES=15

usage() {
  cat <<'EOF'
Usage: scripts/wsl2-login-acceptance.sh [--within-minutes N]

Run this inside Ubuntu WSL2 after signing in through the browser, choosing the
administrator portal, and opening its overview. The script verifies that the
same administrator reached both /api/v1/auth/me and /api/v1/admin/overview and
that GeoChem created the corresponding governance profile and storage quota.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --within-minutes)
      [[ $# -ge 2 ]] || { echo "--within-minutes requires a value" >&2; exit 2; }
      WINDOW_MINUTES="$2"
      shift
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [[ "$(uname -s)" != "Linux" ]] || ! grep -qi microsoft /proc/version 2>/dev/null; then
  echo "This acceptance check must run inside Ubuntu WSL2." >&2
  exit 1
fi
if [[ ! "$WINDOW_MINUTES" =~ ^[1-9][0-9]*$ ]] || (( WINDOW_MINUTES > 1440 )); then
  echo "--within-minutes must be an integer between 1 and 1440." >&2
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

compose=(docker compose --env-file "$ENV_FILE" -f "$ROOT_DIR/deploy/docker-compose.yml")
if ! "${compose[@]}" ps --status running postgres web keycloak >/dev/null 2>&1; then
  echo "GeoChem services are not running. Run scripts/wsl2-deploy.sh first." >&2
  exit 1
fi

login_row="$("${compose[@]}" exec -T postgres psql -U postgres -d geochem -AtF $'\t' -v ON_ERROR_STOP=1 -v window_minutes="$WINDOW_MINUTES" -c \
  "WITH recent_login AS (
     SELECT user_id, username, roles_json, created_at::timestamptz AS logged_in_at
       FROM api_audit_events
      WHERE method='GET' AND path='/api/v1/auth/me' AND status_code=200
        AND user_id <> '' AND roles_json LIKE '%\"admin\"%'
        AND created_at::timestamptz >= NOW() - (:'window_minutes' || ' minutes')::interval
      ORDER BY created_at::timestamptz DESC LIMIT 1
   ), admin_portal AS (
     SELECT user_id, MAX(created_at::timestamptz) AS admin_opened_at
       FROM api_audit_events
      WHERE method='GET' AND path='/api/v1/admin/overview' AND status_code=200
        AND created_at::timestamptz >= NOW() - (:'window_minutes' || ' minutes')::interval
      GROUP BY user_id
   )
   SELECT l.user_id, l.username, l.roles_json, l.logged_in_at, a.admin_opened_at,
          p.status, q.quota_bytes
     FROM recent_login l
     JOIN admin_portal a ON a.user_id=l.user_id
     JOIN user_profiles p ON p.user_id=l.user_id
     JOIN user_storage_quotas q ON q.user_id=l.user_id
    LIMIT 1;" 2>/dev/null || true)"

if [[ -z "$login_row" ]]; then
  cat >&2 <<EOF
No complete administrator login was recorded in the last ${WINDOW_MINUTES} minute(s).

In Windows, open https://${GEOCHEM_SITE_HOST:-geochem.lan}, sign in, choose
"进入管理后台", and wait for the administrator overview to load. Then rerun:

  bash scripts/wsl2-login-acceptance.sh --within-minutes ${WINDOW_MINUTES}
EOF
  exit 1
fi

IFS=$'\t' read -r user_id username roles logged_in_at admin_opened_at profile_status quota_bytes <<<"$login_row"
if [[ "$profile_status" != "active" ]]; then
  echo "The authenticated administrator profile is not active." >&2
  exit 1
fi
if [[ ! "$quota_bytes" =~ ^[0-9]+$ ]] || (( quota_bytes <= 0 )); then
  echo "The authenticated administrator has no valid storage quota." >&2
  exit 1
fi

printf 'PASS  Keycloak login reached the protected GeoChem API\n'
printf 'PASS  The same account opened the administrator overview\n'
printf 'PASS  Governance profile and storage quota were provisioned\n\n'
printf 'User:       %s (%s)\n' "$username" "$user_id"
printf 'Roles:      %s\n' "$roles"
printf 'Login:      %s\n' "$logged_in_at"
printf 'Admin page: %s\n' "$admin_opened_at"
printf 'Quota:      %s bytes\n' "$quota_bytes"
printf '\nWSL2 browser login acceptance passed.\n'
