#!/bin/sh
# The app's and Keycloak's own roles and databases (P1-T8a), on first start of an empty
# cluster only. None is a superuser, and neither the app's roles nor Keycloak's may connect to
# the other's database. The app's database has two roles (P1-T19b): ihr_owner owns it (schema
# changes: migrate, admin, the provisioner); ihr_app, the role everything else connects as, is
# made by scripts/staging.sh up (db_roles_apply), which also gives both their passwords, as
# postgres, before anything connects: until then ihr_owner has none, so nobody can log in as it
# over the network. Keycloak's password comes from the environment (secrets/db.env) through
# psql's \getenv, never argv.
set -eu
psql -v ON_ERROR_STOP=1 -U postgres -d postgres <<'SQL'
\getenv kc_pw KC_DB_PASSWORD
CREATE ROLE ihr_owner LOGIN;
CREATE DATABASE ihr OWNER ihr_owner;
CREATE ROLE keycloak LOGIN PASSWORD :'kc_pw';
CREATE DATABASE keycloak OWNER keycloak;
REVOKE ALL ON DATABASE ihr FROM PUBLIC;
REVOKE ALL ON DATABASE keycloak FROM PUBLIC;
REVOKE ALL ON DATABASE postgres FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE keycloak TO keycloak;
SQL
