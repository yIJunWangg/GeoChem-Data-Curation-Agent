#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${GEOCHEM_ENV_FILE:-$ROOT_DIR/deploy/.env}"
COMPOSE_FILE="$ROOT_DIR/deploy/docker-compose.yml"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing deployment environment: $ENV_FILE" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
kcadm=(/opt/keycloak/bin/kcadm.sh --config /tmp/geochem-kcadm.config)

"${compose[@]}" exec -T keycloak "${kcadm[@]}" config credentials \
  --server http://127.0.0.1:8080 \
  --realm master \
  --user "${KEYCLOAK_ADMIN_USERNAME:-admin}" \
  --password "$KEYCLOAK_ADMIN_PASSWORD" >/dev/null

client_uuid="$(
  "${compose[@]}" exec -T keycloak "${kcadm[@]}" get clients \
    -r geochem -q clientId=geochem-web --fields id --format csv --noquotes \
    | tr -d '\r' | sed -n '1p'
)"
if [[ -z "$client_uuid" ]]; then
  echo "Keycloak client geochem-web was not found in realm geochem." >&2
  exit 1
fi

site_origin="https://${GEOCHEM_SITE_HOST}"
site_redirect="${site_origin}/*"
logout_redirect="$site_redirect"

"${compose[@]}" exec -T keycloak "${kcadm[@]}" update "clients/$client_uuid" \
  -r geochem \
  -s "rootUrl=$site_origin" \
  -s "baseUrl=$site_origin/" \
  -s "redirectUris=[\"$site_redirect\"]" \
  -s "webOrigins=[\"$site_origin\"]" \
  -s "attributes={\"pkce.code.challenge.method\":\"S256\",\"post.logout.redirect.uris\":\"$logout_redirect\"}" \
  >/dev/null

echo "Keycloak geochem-web client now accepts $site_origin."

admin_client_id="geochem-admin-api"
admin_client_uuid="$(
  "${compose[@]}" exec -T keycloak "${kcadm[@]}" get clients \
    -r geochem -q "clientId=$admin_client_id" --fields id --format csv --noquotes \
    | tr -d '\r' | sed -n '1p'
)"

if [[ -z "$admin_client_uuid" ]]; then
  "${compose[@]}" exec -T keycloak "${kcadm[@]}" create clients -r geochem \
    -s "clientId=$admin_client_id" \
    -s 'name=GeoChem in-app user administration' \
    -s enabled=true \
    -s publicClient=false \
    -s bearerOnly=false \
    -s standardFlowEnabled=false \
    -s directAccessGrantsEnabled=false \
    -s serviceAccountsEnabled=true \
    -s "secret=$KEYCLOAK_ADMIN_API_CLIENT_SECRET" >/dev/null
  admin_client_uuid="$(
    "${compose[@]}" exec -T keycloak "${kcadm[@]}" get clients \
      -r geochem -q "clientId=$admin_client_id" --fields id --format csv --noquotes \
      | tr -d '\r' | sed -n '1p'
  )"
else
  "${compose[@]}" exec -T keycloak "${kcadm[@]}" update "clients/$admin_client_uuid" -r geochem \
    -s enabled=true \
    -s publicClient=false \
    -s standardFlowEnabled=false \
    -s directAccessGrantsEnabled=false \
    -s serviceAccountsEnabled=true \
    -s "secret=$KEYCLOAK_ADMIN_API_CLIENT_SECRET" >/dev/null
fi

if [[ -z "$admin_client_uuid" ]]; then
  echo "Unable to create Keycloak service client $admin_client_id." >&2
  exit 1
fi

# This service account can manage users and inspect their role mappings, but it
# is deliberately not granted realm-admin or client administration privileges.
service_username="service-account-$admin_client_id"
"${compose[@]}" exec -T keycloak "${kcadm[@]}" add-roles \
  -r geochem \
  --uusername "$service_username" \
  --cclientid realm-management \
  --rolename query-users \
  --rolename view-users \
  --rolename manage-users >/dev/null

echo "Keycloak $admin_client_id service account is ready for in-app user management."

initial_admin_username="${GEOCHEM_INITIAL_ADMIN_USERNAME:-geochem-admin}"
initial_admin_uuid="$(
  "${compose[@]}" exec -T keycloak "${kcadm[@]}" get users \
    -r geochem -q "username=$initial_admin_username" --fields id,username --format csv --noquotes \
    | tr -d '\r' | awk -F, -v username="$initial_admin_username" '$2 == username { print $1; exit }'
)"

if [[ -z "$initial_admin_uuid" ]]; then
  create_args=(
    create users -r geochem
    -s "username=$initial_admin_username"
    -s enabled=true
    -s emailVerified=false
    -s 'requiredActions=["UPDATE_PASSWORD"]'
  )
  if [[ -n "${GEOCHEM_INITIAL_ADMIN_EMAIL:-}" ]]; then
    create_args+=(-s "email=$GEOCHEM_INITIAL_ADMIN_EMAIL")
  fi
  "${compose[@]}" exec -T keycloak "${kcadm[@]}" "${create_args[@]}" >/dev/null
  initial_admin_uuid="$(
    "${compose[@]}" exec -T keycloak "${kcadm[@]}" get users \
      -r geochem -q "username=$initial_admin_username" --fields id,username --format csv --noquotes \
      | tr -d '\r' | awk -F, -v username="$initial_admin_username" '$2 == username { print $1; exit }'
  )"
  if [[ -z "$initial_admin_uuid" ]]; then
    echo "Unable to create initial GeoChem administrator $initial_admin_username." >&2
    exit 1
  fi
  "${compose[@]}" exec -T keycloak "${kcadm[@]}" set-password \
    -r geochem --userid "$initial_admin_uuid" \
    --new-password "$GEOCHEM_INITIAL_ADMIN_PASSWORD" --temporary >/dev/null
  echo "Created initial GeoChem administrator $initial_admin_username with a temporary password."
else
  echo "Initial GeoChem administrator $initial_admin_username already exists; its password was not changed."
fi

admin_role_present="$(
  "${compose[@]}" exec -T keycloak "${kcadm[@]}" get \
    "users/$initial_admin_uuid/role-mappings/realm/composite" -r geochem \
    --fields name --format csv --noquotes | tr -d '\r' | grep -Fx admin || true
)"
if [[ -z "$admin_role_present" ]]; then
  "${compose[@]}" exec -T keycloak "${kcadm[@]}" add-roles \
    -r geochem --uid "$initial_admin_uuid" --rolename admin >/dev/null
  echo "Assigned the GeoChem admin role to $initial_admin_username."
fi
