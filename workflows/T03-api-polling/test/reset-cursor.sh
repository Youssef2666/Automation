#!/usr/bin/env bash
# Reset the T03 cursor (and optionally the audit row) so the next run re-reads the feed from the start.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
docker compose exec -T redis redis-cli set t03:events:cursor 0
docker compose exec -T postgres psql -U "${POSTGRES_USER:-n8n}" -d "${DEMO_DB:-demo}" -c "update sync_state set cursor='0', rows_seen=0 where source='mock-api-events'"
echo "cursor reset - run: python scripts/dev/run-workflow.py T03"
