#!/usr/bin/env bash
# Drop and recreate ONLY the demo database from seed/schema.sql + seed/seed.sql (n8n's own db is untouched).
set -euo pipefail
export MSYS_NO_PATHCONV=1  # Git Bash: keep /container/paths intact
cd "$(dirname "${BASH_SOURCE[0]}")/.."
set -a; [ -f .env ] && . ./.env; set +a
PGUSER=${POSTGRES_USER:-n8n}; N8NDB=${POSTGRES_DB:-n8n}; DEMO=${DEMO_DB:-demo}

echo ">> terminating connections and dropping ${DEMO}"
docker compose exec -T postgres psql -U "$PGUSER" -d "$N8NDB" -v ON_ERROR_STOP=1 \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${DEMO}' AND pid <> pg_backend_pid();" \
  -c "DROP DATABASE IF EXISTS \"${DEMO}\";" -c "CREATE DATABASE \"${DEMO}\";"
echo ">> loading schema + seed"
docker compose exec -T postgres psql -U "$PGUSER" -d "$DEMO" -v ON_ERROR_STOP=1 -f /seed/schema.sql -f /seed/seed.sql >/dev/null
docker compose exec -T postgres psql -U "$PGUSER" -d "$DEMO" -tAc "select 'customers='||count(*) from customers"
echo ">> done"
