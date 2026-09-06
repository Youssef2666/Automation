#!/usr/bin/env bash
# Runs once on first Postgres start (docker-entrypoint-initdb.d): creates the demo database next to n8n's own
# database and loads seed/schema.sql + seed/seed.sql (mounted read-only at /seed). Re-run later with scripts/reseed.sh.
set -euo pipefail
DEMO="${DEMO_DB:-demo}"
echo ">> creating demo database '${DEMO}'"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -c "CREATE DATABASE \"${DEMO}\";"
if [ -f /seed/schema.sql ]; then
  echo ">> loading /seed/schema.sql"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$DEMO" -f /seed/schema.sql >/dev/null
fi
if [ -f /seed/seed.sql ]; then
  echo ">> loading /seed/seed.sql"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$DEMO" -f /seed/seed.sql >/dev/null
  psql --username "$POSTGRES_USER" --dbname "$DEMO" -tAc "select 'customers='||count(*) from customers"
fi
echo ">> demo database ready"
