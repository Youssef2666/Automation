#!/usr/bin/env bash
# Reset D04: drop the watermark row and empty the mirror so the next run is a full first sync again.
# Also restart mock-api so any PATCHed product (see test/README.md) goes back to the seed values.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
docker compose exec -T postgres psql -U "${POSTGRES_USER:-n8n}" -d "${DEMO_DB:-demo}" \
  -c "delete from sync_state where source = 'mock-api-products'" \
  -c "truncate products_mirror"
docker compose restart mock-api >/dev/null
echo "watermark + mirror reset, mock-api reseeded - run: python scripts/dev/run-workflow.py D04"
