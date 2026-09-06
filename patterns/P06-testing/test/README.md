# P06 test plan

- `cases.json` - the plan (6 cases: mock-api health/outage, P01 fixture webhook, T01 valid/invalid/unauthenticated).
  The authoring script embeds the same list into the workflow's "Test plan" node - edit `CASES` there and regenerate
  to keep both runners in sync.
- `replay.py [cases.json]` - runs the plan from the host with the standard library; exit code 1 on failure.

```bash
python patterns/P06-testing/test/replay.py
python scripts/dev/run-workflow.py P06
```
