# P07 test fixtures

- `input.json` - the item a caller passes to the Execute Workflow node (POST to `mock-api:8080/secure/ping`; the
  key is not in it - the credential adds the header).
- `expected.json` - the flat item that comes back for that input, for a GET, and the Stop and Error messages for a
  refused host and a missing url.
- `run.py` - live check. Sub-workflows cannot be started from the CLI with input, so it builds a throwaway caller
  with the builder DSL (Manual Trigger → cases → Execute Workflow P07 with the error lane kept), imports it as
  `ALP07SecretsTest`, runs it with `n8n execute`, reads the execution through the public API, asserts the three
  cases (and that `lab-demo-key` never appears in execution data), then deletes the caller. `--verbose` dumps every
  lane, `--keep` leaves the caller in n8n.

```bash
python patterns/P07-secrets/test/run.py            # 3 passed, 0 failed
python scripts/dev/executions.py --workflow ALP07Secrets0000 --last 3
```

The `pre-commit` file one level up is the local guard described in the README (`cp` it into `.git/hooks/`).
