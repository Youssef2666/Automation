#!/usr/bin/env bash
# Seed N (default 8) tiny fake "old" dump objects under artifacts/db-dumps/ so the next D05 run has something
# to rotate away (keep = 7). Keys follow the real naming (demo-<yyyyLLdd-HHmmss>.zip) with January dates, so they
# sort before every real dump and are the first to expire. Contents are a one-line text, not a real zip.
#
#   bash workflows/D05-db-dump-to-minio/test/seed-old-dumps.sh        # 8 objects
#   bash workflows/D05-db-dump-to-minio/test/seed-old-dumps.sh 3      # 3 objects
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
N=${1:-8}
# Dummy lab credentials from .env.example (override with MINIO_ROOT_USER / MINIO_ROOT_PASSWORD if you changed them).
docker compose exec -T minio mc alias set local http://localhost:9000 \
  "${MINIO_ROOT_USER:-minioadmin}" "${MINIO_ROOT_PASSWORD:-minioadmin}" >/dev/null
for i in $(seq 1 "$N"); do
  day=$(printf '%02d' "$i")
  key="db-dumps/demo-202601${day}-020000.zip"
  printf 'fake old dump %s (seeded by test/seed-old-dumps.sh, not a real zip)\n' "$key" \
    | docker compose exec -T minio mc pipe "local/artifacts/$key" >/dev/null
  echo "seeded s3://artifacts/$key"
done
echo "--- artifacts/db-dumps/ now:"
docker compose exec -T minio mc ls local/artifacts/db-dumps/
