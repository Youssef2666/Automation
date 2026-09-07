#!/usr/bin/env bash
# Simulate what a real source does between two runs. json-server accepts PATCH and keeps the change in the
# container only (db.json is copied into the image; `docker compose restart mock-api` restores the seed).
#
#   bash workflows/D04-incremental-sync/test/simulate-source-change.sh content   # stock 20 -> 7  => 1 updated
#   bash workflows/D04-incremental-sync/test/simulate-source-change.sh touch     # only updated_at => 1 skipped
set -euo pipefail
MODE="${1:-content}"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
case "$MODE" in
  content) BODY="{\"stock\": 7, \"updated_at\": \"$NOW\"}" ;;
  touch)   BODY="{\"updated_at\": \"$NOW\"}" ;;
  *) echo "usage: $0 content|touch"; exit 2 ;;
esac
curl -s -X PATCH http://localhost:8080/products/20 -H 'Content-Type: application/json' -d "$BODY"
echo
echo "product 20 patched ($MODE, updated_at=$NOW) - run: python scripts/dev/run-workflow.py D04"
