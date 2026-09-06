#!/usr/bin/env bash
# One-shot setup after `docker compose --profile core up -d`:
# waits for n8n, creates the owner + API key, imports canonical credentials, imports every workflow
# in workflows/ and patterns/, and publishes the ones marked `autopublish: true`.
# Safe to re-run. Pass-through flags go to scripts/bootstrap.py (e.g. --skip-workflows, --publish-all).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PY=${PYTHON:-python}
command -v "$PY" >/dev/null 2>&1 || PY=python3

if [ ! -f .env ]; then
  echo "!! .env missing - creating it from .env.example (edit passwords before exposing anything)"
  cp .env.example .env
fi

if ! docker compose ps --status running --services 2>/dev/null | grep -qx n8n; then
  echo "!! n8n container is not running. Start the stack first:"
  echo "   docker compose --profile core up -d"
  exit 1
fi

"$PY" scripts/bootstrap.py "$@"
