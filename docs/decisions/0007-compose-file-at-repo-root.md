# 0007 - Compose file at the repo root, image sources under docker/

Date: 2026-09-06
Status: accepted

**Context**: PRD section 7 puts `docker-compose.yml` inside `docker/`. Compose loads `.env` from the compose file's
directory, so `docker compose up` from the repo root ignored `./.env` unless every command carried `--env-file` or
`-f docker/docker-compose.yml`, and bind mounts (`./seed`, `./data`, `./workflows`) had to climb a level.
**Decision**: `docker-compose.yml` lives at the repo root (project `automation-lab`). `docker/` keeps what builds or
initialises containers: `docker/mock-api/`, `docker/docgen/`, `docker/postgres/init/`. Scripts `cd` to the repo root
and call plain `docker compose`; `.claude/settings.json` sets `COMPOSE_FILE` so hooks find it either way.
**Consequences**: The four-command quick start works as written and `.env` is picked up automatically; the
`compose-smoke` job watches both `docker/**` and `docker-compose.yml`. Readers following the PRD tree literally
should read this ADR; the stack skill heading still names the old path and is updated with the next stack change.
