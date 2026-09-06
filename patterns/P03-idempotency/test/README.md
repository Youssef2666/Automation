# P03 test fixtures

`input.json` is the item a caller passes to the Execute Workflow node; `expected.json` the item that comes back on
the first and on the second call. Sub-workflows cannot be started from the CLI with input, so the live check goes
through T01:

```bash
curl -s -X POST localhost:5678/webhook/t01-orders -H 'Content-Type: application/json' -H 'X-Lab-Key: lab-demo-key' -d @../../../workflows/T01-webhook-to-database/test/payload.json
docker compose exec -T redis redis-cli get idem:t01-orders:evt-order-20260901-0001    # 1, then 2 after a replay
docker compose exec -T redis redis-cli ttl idem:t01-orders:evt-order-20260901-0001    # ~86400
python scripts/dev/executions.py --workflow ALP03Idempotency --last 2                 # mode=integrated, success
```
