#!/bin/sh
# The app's and Keycloak's own roles and databases (P1-T8a), on first start of an empty
# cluster only. Neither is a superuser, and neither may connect to the other's database.
# Passwords come from the environment (secrets/db.env) through psql's \getenv, never argv.
set -eu
psql -v ON_ERROR_STOP=1 -U postgres -d postgres <<'SQL'
\getenv ihr_pw IHR_DB_PASSWORD
\getenv kc_pw KC_DB_PASSWORD
CREATE ROLE ihr LOGIN PASSWORD :'ihr_pw';
CREATE DATABASE ihr OWNER ihr;
CREATE ROLE keycloak LOGIN PASSWORD :'kc_pw';
CREATE DATABASE keycloak OWNER keycloak;
REVOKE ALL ON DATABASE ihr FROM PUBLIC;
REVOKE ALL ON DATABASE keycloak FROM PUBLIC;
REVOKE ALL ON DATABASE postgres FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE ihr TO ihr;
GRANT CONNECT, TEMPORARY ON DATABASE keycloak TO keycloak;
SQL
