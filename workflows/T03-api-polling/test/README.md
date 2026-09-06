# T03 test inputs

- `sample-events.json` - two events as served by `GET http://mock-api:8080/events?id_gte=1&_sort=id&_order=asc&_limit=2`
  (the shape the Split events / Insert nodes see).
- `reset-cursor.sh` - puts the cursor back to 0 so the feed is replayed; the unique key on
  `webhook_events.external_id` rejects the duplicates row by row.

Run: `python scripts/dev/run-workflow.py T03` (see the README for what to check afterwards).
