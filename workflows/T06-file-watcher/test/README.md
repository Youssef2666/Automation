# T06 test inputs

`sample-drop.csv` - copy it into `data/inbox/` while the workflow is published:

```bash
cp workflows/T06-file-watcher/test/sample-drop.csv data/inbox/
```

Expected within ~10 s: an execution (mode trigger), `data/out/processed-sample-drop.csv.receipt.json` with the
SHA-256 and `csv_rows: 2`, a `documents` row (kind `inbox-file`), and `artifacts/inbox/sample-drop.csv` in MinIO.
CLI runs (`python scripts/dev/run-workflow.py T06`) use the same path via the "Sample file (manual runs)" node.
