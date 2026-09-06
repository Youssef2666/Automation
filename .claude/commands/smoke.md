---
description: Full local smoke test - bring up the core profile, bootstrap, import everything, fire T01, check Mailpit/Postgres, and report clone-to-first-execution time.
argument-hint: [--profiles core,docs,ai]
allowed-tools: Bash(docker compose:*), Bash(docker:*), Bash(bash scripts/*), Bash(python:*), Bash(curl:*), Read
---

Run the smoke sequence and time it (this is PRD goal G2: < 10 minutes on a clean machine):

1. `docker info` (stop if the daemon is down).
2. `docker compose --profile core up -d` (add profiles from `$ARGUMENTS`), wait for `http://localhost:5678/healthz`.
3. `bash scripts/setup.sh` - owner, API key, credentials, import, publish.
4. `python scripts/dev/smoke.py` - posts `workflows/T01-webhook-to-database/test/payload.json` to the T01 webhook,
   checks the row in `demo.webhook_events`, sends a Mailpit test, lists MinIO buckets, and prints elapsed time.
5. Summarise: services up, workflows imported (count), T01 execution id, elapsed minutes. List every failure with the
   exact error and the file to fix.
