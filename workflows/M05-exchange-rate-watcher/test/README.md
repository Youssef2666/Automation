# M05 test inputs

M05 has no external input: the provider is the local mock-api (`GET http://mock-api:8080/rates/latest`) and the
baseline is the seeded `rate_history` table (USD → EUR/GBP/LYD, ten daily points each).

- `rates-sample.json` - one provider response, the shape P02 hands to **Compare with history** under `data`.
  Paste it as pinned output on **Fetch rates (P02)** in the editor to exercise the Code node without the network.
- `history-check.sql` - the "previous rate" query, the rows the last runs stored, and the per-pair change as the
  workflow computes it. Run it after each execution to check the figures in the e-mail.

```bash
python scripts/dev/run-workflow.py M05
docker compose exec -T postgres psql -U n8n -d demo < workflows/M05-exchange-rate-watcher/test/history-check.sql
curl -s "localhost:8025/api/v1/messages?limit=1" | python -m json.tool | grep Subject
docker compose exec -T redis redis-cli --scan --pattern "m05:alert:*"     # one key per alerted pair, 6 h TTL
```

To force the "within threshold" path set `threshold_pct` in **Config** to `50`; to reset the cooldown:
`docker compose exec -T redis redis-cli --scan --pattern "m05:alert:*" | xargs -r docker compose exec -T redis redis-cli del`.
