#!/usr/bin/env python
"""restore-table.py [table] [--key db-dumps/demo-<run_id>.zip]

Round-trip check for a D05 dump: fetch the newest (or the given) dump from MinIO, unzip <table>.json, load the
rows into a scratch table `<table>_restore_check` with json_populate_recordset(null::<table>, ...), compare the
count with the manifest, print a sample row, and drop the scratch table. Nothing in the live table is touched.

    python workflows/D05-db-dump-to-minio/test/restore-table.py customers
    python workflows/D05-db-dump-to-minio/test/restore-table.py orders --key db-dumps/demo-20260907-115909.zip

Needs: stack up (docker compose), python 3. Uses the dummy MinIO credentials from .env.example unless
MINIO_ROOT_USER / MINIO_ROOT_PASSWORD are set.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BUCKET = "artifacts"
PREFIX = "db-dumps/"


def sh(*args: str, input_: bytes | None = None) -> bytes:
    res = subprocess.run(["docker", "compose", *args], cwd=REPO, input=input_, capture_output=True, check=False)
    if res.returncode != 0:
        sys.stderr.write(res.stderr.decode("utf-8", "replace"))
        raise SystemExit(f"command failed: docker compose {' '.join(args)}")
    return res.stdout


def mc(*args: str, input_: bytes | None = None) -> bytes:
    return sh("exec", "-T", "minio", "mc", *args, input_=input_)


def psql(sql: str) -> str:
    out = sh("exec", "-T", "postgres", "psql", "-U", "n8n", "-d", "demo", "-v", "ON_ERROR_STOP=1", "-qAt", "-f", "-",
             input_=sql.encode("utf-8"))
    return out.decode("utf-8", "replace")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    table = args[0] if args else "customers"
    key = None
    if "--key" in sys.argv:
        key = sys.argv[sys.argv.index("--key") + 1]
    user = os.environ.get("MINIO_ROOT_USER", "minioadmin")
    password = os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin")
    mc("alias", "set", "local", "http://localhost:9000", user, password)
    if key is None:
        listing = mc("ls", f"local/{BUCKET}/{PREFIX}").decode("utf-8", "replace")
        names = sorted(line.split()[-1] for line in listing.splitlines() if line.strip().endswith(".zip"))
        if not names:
            raise SystemExit(f"no dumps under s3://{BUCKET}/{PREFIX} - run D05 first")
        key = PREFIX + names[-1]
    print(f"dump: s3://{BUCKET}/{key}")
    blob = mc("cat", f"local/{BUCKET}/{key}")
    zf = zipfile.ZipFile(io.BytesIO(blob))
    manifest = json.loads(zf.read("manifest.json"))
    entry = next((t for t in manifest["tables"] if t["table"] == table), None)
    if entry is None:
        raise SystemExit(f"table {table!r} not in the dump; tables: {', '.join(t['table'] for t in manifest['tables'])}")
    doc = json.loads(zf.read(entry["file"]))
    rows = doc["rows"]
    print(f"manifest: run_id={manifest['run_id']} database={manifest['database']} server={manifest['server_version']} "
          f"tables={manifest['table_count']} total_rows={manifest['total_rows']}")
    print(f"{table}: {doc['row_count']} rows, columns {doc['columns']}")

    scratch = f"{table}_restore_check"
    payload = json.dumps(rows).replace("$d05$", "$ d05 $")
    sql = f"""
drop table if exists {scratch};
create table {scratch} as select * from {table} with no data;
insert into {scratch} select * from json_populate_recordset(null::{table}, $d05${payload}$d05$);
select count(*) from {scratch};
select row_to_json(r) from (select * from {scratch} limit 1) r;
drop table {scratch};
"""
    out = [line for line in psql(sql).splitlines() if line.strip()]
    restored = int(out[0]) if out else -1
    print(f"restored into {scratch}: {restored} rows (manifest says {entry['row_count']})")
    if len(out) > 1:
        print(f"sample restored row: {out[1][:300]}")
    ok = restored == entry["row_count"] == doc["row_count"]
    print("ROUND-TRIP OK" if ok else "ROUND-TRIP MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
