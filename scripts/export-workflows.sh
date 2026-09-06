#!/usr/bin/env bash
# Export workflows from the running n8n back into their repo folders (workflow-as-code loop).
#
#   bash scripts/export-workflows.sh            # all workflows whose id maps to a folder
#   bash scripts/export-workflows.sh T01 P03    # only these catalog ids
#
# Uses `n8n export:workflow --backup` inside the container, then scripts/dev/strip-export.py removes
# credential values, pinData, instance metadata and re-pretty-prints so diffs stay readable.
set -euo pipefail
export MSYS_NO_PATHCONV=1  # Git Bash: keep /container/paths intact
cd "$(dirname "${BASH_SOURCE[0]}")/.."

OUT=/home/node/.n8n-files/data/export/raw
docker compose exec -T n8n sh -c "rm -rf $OUT && mkdir -p $OUT"
docker compose exec -T n8n n8n export:workflow --backup --output="$OUT/"
python scripts/dev/strip-export.py data/export/raw "$@"
echo "review with: git status --short workflows patterns && python scripts/validate.py"
