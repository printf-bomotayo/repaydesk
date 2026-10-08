#!/bin/sh
# One Postgres instance holds four databases, each owned by its own role.
# Fineract gets two (tenant registry and the default tenant), Odoo gets one it creates
# itself, and Repaydesk gets one. No service can read another's database.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<EOSQL
  CREATE ROLE fineract LOGIN PASSWORD '${FINERACT_DB_PASSWORD}';
  CREATE DATABASE fineract_tenants OWNER fineract;
  CREATE DATABASE fineract_default OWNER fineract;

  CREATE ROLE odoo LOGIN CREATEDB PASSWORD '${ODOO_DB_PASSWORD}';

  CREATE ROLE repaydesk LOGIN PASSWORD '${REPAYDESK_DB_PASSWORD}';
  CREATE DATABASE repaydesk OWNER repaydesk;
EOSQL

# Langfuse (optional, compose.langfuse.yaml). Created always; costs nothing when unused.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<EOSQL
  CREATE ROLE langfuse LOGIN PASSWORD '${LANGFUSE_DB_PASSWORD:-langfuse-unused}';
  CREATE DATABASE langfuse OWNER langfuse;
EOSQL
