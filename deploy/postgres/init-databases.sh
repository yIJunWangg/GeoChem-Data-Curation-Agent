#!/usr/bin/env sh
set -eu

psql --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=geochem_password="$GEOCHEM_DB_PASSWORD" \
  --set=keycloak_password="$KEYCLOAK_DB_PASSWORD" <<'EOSQL'
CREATE ROLE geochem LOGIN PASSWORD :'geochem_password';
CREATE DATABASE geochem OWNER geochem ENCODING 'UTF8';

CREATE ROLE keycloak LOGIN PASSWORD :'keycloak_password';
CREATE DATABASE keycloak OWNER keycloak ENCODING 'UTF8';
EOSQL

