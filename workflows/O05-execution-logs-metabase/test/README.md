# O05 test inputs

O05 reads n8n's own execution store through the public API, so the "input" is whatever has run on the stack.
Files here:

- `schema-addon.sql` - the unique index the workflow creates on its first run, plus the duplicate pre-check.
- `metabase-queries.sql` - the five SQL questions for the Metabase dashboard (same SQL as `docs/observability.md`).

```bash
docker compose exec -T postgres psql -U n8n -d demo < workflows/O05-execution-logs-metabase/test/schema-addon.sql
python scripts/dev/run-workflow.py O05                       # first run: inserted N
python scripts/dev/run-workflow.py O05                       # second run: inserted 0 (idempotent)
docker compose exec -T postgres psql -U n8n -d demo -c "select body from notifications where channel = 'log' order by id desc limit 1"
docker compose exec -T postgres psql -U n8n -d demo < workflows/O05-execution-logs-metabase/test/metabase-queries.sql
```
