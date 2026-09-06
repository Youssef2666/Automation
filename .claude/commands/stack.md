---
description: Manage the local Docker stack - up/down/status/logs/reset for compose profiles (core, docs, ai, observability, queue).
argument-hint: up|down|status|logs|restart [profile ...] [service]
allowed-tools: Bash(docker compose:*), Bash(docker:*), Bash(curl:*), Bash(bash scripts/*), Read
---

Manage the stack defined in `docker/docker-compose.yml` (run from repo root). Arguments: `$ARGUMENTS`.

- `up [profiles]` -> `docker compose --profile core [--profile X ...] up -d`, then wait for
  `curl -s http://localhost:5678/healthz` to return ok and print the service table (`docker compose ps`).
  Default profile is `core` when none is given.
- `down` -> `docker compose --profile core --profile docs --profile ai --profile observability --profile queue down`
  (keeps volumes; volume wipes require the user to ask explicitly).
- `status` -> `docker compose ps` + health of n8n, mock-api, mailpit, minio; note which profiles are up.
- `logs [service]` -> `docker compose logs --tail 200 [service]`.
- `restart [service]` -> `docker compose restart [service]`.

If the Docker daemon is not running, say so and stop. After `up`, remind the user of `bash scripts/setup.sh` if
`data/.bootstrapped` does not exist.
