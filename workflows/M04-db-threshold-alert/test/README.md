# M04 test inputs

M04 reads only the demo database, so the "input" is the seed. `thresholds-check.sql` is the metrics query the
workflow runs plus the detail behind `sla_breached`; execute it to know what the e-mail will say:

```bash
docker compose exec -T postgres psql -U n8n -d demo < workflows/M04-db-threshold-alert/test/thresholds-check.sql
python scripts/dev/run-workflow.py M04                      # first run this hour: e-mail + notifications row
python scripts/dev/run-workflow.py M04                      # second run: suppressed (still logged via P08)
curl -s "localhost:8025/api/v1/messages?limit=1" | python -m json.tool | grep Subject
docker compose exec -T redis redis-cli keys "m04:alert:*"   # one key per breached metric, value = runs this hour
```

To see the "all within thresholds" path, raise the limits in the **Thresholds** node (`pending_orders_max` 100,
`pending_age_hours_max` 5000, `sla_breached_max` 100) and run again: no e-mail, one `info` row in `execution_log`.
