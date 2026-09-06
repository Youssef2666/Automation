# T02 test inputs

T02 reads only the demo database, so the "input" is the seed. `queries.sql` holds the three summaries the workflow
runs; execute it in psql to know what the e-mail should say before running the workflow:

```bash
docker compose exec -T postgres psql -U n8n -d demo < workflows/T02-daily-digest/test/queries.sql
python scripts/dev/run-workflow.py T02
```
