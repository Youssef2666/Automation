# 0002 - Postgres for n8n persistence instead of SQLite

Date: 2026-09-06
Status: accepted

**Context**: n8n defaults to SQLite, which would make the `core` profile one container lighter. The PRD already
needs Postgres for the `demo` database workflows read and write, and queue mode (`queue` profile) needs it too.
**Decision**: One `postgres:17-alpine` service holds two databases: `n8n` (engine, `DB_TYPE=postgresdb`) and
`demo` (created by `docker/postgres/init/01-demo-db.sh` from `seed/schema.sql` + `seed/seed.sql`). The
`Postgres - demo` credential points only at `demo`; workflows never query n8n's own tables (O05 uses the public API).
**Consequences**: Same engine locally and in the compose smoke test; `EXECUTIONS_MODE=queue` works by adding the
`queue` profile; `bash scripts/reseed.sh` drops and reloads `demo` without touching n8n state. Cost: one more
healthcheck at boot and a 5432 host port to close before exposing the stack.
