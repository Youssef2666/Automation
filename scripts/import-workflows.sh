#!/usr/bin/env bash
# Import workflow folders into the running n8n container via the n8n CLI.
#
#   bash scripts/import-workflows.sh                       # every folder (patterns first)
#   bash scripts/import-workflows.sh workflows/T01-webhook-to-database patterns/P01-error-handler
#   bash scripts/import-workflows.sh --publish workflows/T01-webhook-to-database
#
# --publish activates the imported workflows (otherwise only README `autopublish: true` ones are, when
# run through scripts/setup.sh). Requires: stack up, `bash scripts/setup.sh` run once (API key).
set -euo pipefail
export MSYS_NO_PATHCONV=1  # Git Bash: keep /container/paths intact
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PUBLISH=0
FOLDERS=()
for arg in "$@"; do
  case "$arg" in
    --publish) PUBLISH=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) FOLDERS+=("${arg%/}") ;;
  esac
done

if [ ${#FOLDERS[@]} -eq 0 ]; then
  for d in patterns/*/ workflows/*/; do
    [ -f "$d/workflow.json" ] && FOLDERS+=("${d%/}")
  done
fi
[ ${#FOLDERS[@]} -gt 0 ] || { echo "no workflow folders found"; exit 1; }

STAGING=/tmp/automation-lab-import-$$-$RANDOM   # unique per run so parallel imports do not collide
docker compose exec -T n8n sh -c "mkdir -p $STAGING"
IDS=()
for f in "${FOLDERS[@]}"; do
  [ -f "$f/workflow.json" ] || { echo "skip $f (no workflow.json)"; continue; }
  name=$(basename "$f")
  docker compose exec -T n8n sh -c "cp /repo/$f/workflow.json $STAGING/$name.json"
  IDS+=("$(python - "$f/workflow.json" <<'EOF'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8")).get("id", ""))
EOF
)")
  echo "staged $f"
done

echo "importing ${#IDS[@]} workflow(s)..."
docker compose exec -T n8n n8n import:workflow --separate --input="$STAGING"
docker compose exec -T n8n sh -c "rm -rf $STAGING"

# import:workflow always leaves a workflow inactive (even when it was active before), so re-publish what needs
# to be live: everything with --publish, otherwise the folders whose README says `autopublish: true`.
i=0
for f in "${FOLDERS[@]}"; do
  id="${IDS[$i]:-}"; i=$((i + 1))
  [ -n "$id" ] || continue
  if [ "$PUBLISH" -eq 1 ] || grep -qE '^autopublish:[[:space:]]*true' "$f/README.md" 2>/dev/null; then
    python scripts/dev/publish.py "$id" || docker compose exec -T n8n n8n publish:workflow --id="$id"
  fi
done
echo "done"
