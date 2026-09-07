#!/usr/bin/env python
"""D05 - Scheduled DB Dump to MinIO with Rotation (keeps the newest 7 dumps).

Daily at 02:00 / manual -> Config (bucket, prefix, keep=7, run_id) -> List tables (information_schema, one item per
base table in `public`) -> Dump table (one `SELECT count(*), json_agg(t)` per table: Postgres serialises every row
to JSON itself, so types stay faithful) -> Build dump files (Code: one <table>.json per table + manifest.json as
binary properties on one item) -> Zip dump (Compression) -> Upload to MinIO artifacts/db-dumps/demo-<run_id>.zip
-> List dumps -> Plan rotation (Code: newest `keep` kept, the rest expire; fails if the fresh upload is missing
from the listing) -> Anything to delete? -> Delete expired dump (one S3 delete per key) -> Summarize run ->
Record dump (documents, kind `db-dump`) -> P08 logs the run.

Execute Command is disabled in this lab, so there is no pg_dump: the dump is logical (JSON per table) and
restorable with plain psql via json_populate_recordset (see README / test/restore-table.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, catalog_id, code, compress, cond_num, execute_workflow, if_,  # noqa: E402
                         manual_trigger, postgres_insert, postgres_query, s3_delete, s3_list, s3_upload, schedule,
                         set_fields, split_out)

LIST_TABLES_SQL = """select table_name,
       quote_ident(table_name)                 as ident,
       current_database()                      as database,
       current_setting('server_version')       as server_version
  from information_schema.tables
 where table_schema = $1 and table_type = 'BASE TABLE'
 order by table_name"""

# One query per input item (table). $1 is the plain table name for the output column; the FROM clause uses the
# quote_ident() form produced by the previous node, so odd identifiers cannot break the statement.
DUMP_TABLE_SQL = """=select $1::text                                   as table_name,
       count(*)::int                              as row_count,
       coalesce(json_agg(row_to_json(t)), '[]'::json) as rows
  from {{ $json.ident }} t"""

BUILD_FILES_JS = r"""
// One input item per table ({table_name, row_count, rows[]}). Emit ONE item whose binary properties are the
// per-table JSON files plus manifest.json, so the Compression node can zip them together (comma-separated list).
const cfg = $('Config').first().json;
const meta = $('List tables').first().json;
const dumped_at = new Date().toISOString();
const tables = $input.all().map(i => i.json).filter(t => t && t.table_name);
if (!tables.length) throw new Error('D05: no tables found in schema ' + cfg.schema + ' - nothing to dump');

const binary = {};
const props = [];
const manifestTables = [];
let total_rows = 0;
let bytes_uncompressed = 0;
for (const t of tables) {
  const rows = Array.isArray(t.rows) ? t.rows : [];
  const columns = rows.length ? Object.keys(rows[0]) : [];
  const doc = { database: meta.database, schema: cfg.schema, table: t.table_name, dumped_at, run_id: cfg.run_id,
                row_count: Number(t.row_count) || rows.length, columns, rows };
  const text = JSON.stringify(doc);
  const file = `${t.table_name}.json`;
  const prop = `t_${t.table_name}`;
  binary[prop] = await this.helpers.prepareBinaryData(Buffer.from(text, 'utf8'), file, 'application/json');
  props.push(prop);
  total_rows += doc.row_count;
  bytes_uncompressed += Buffer.byteLength(text, 'utf8');
  manifestTables.push({ table: t.table_name, file, row_count: doc.row_count, bytes: Buffer.byteLength(text, 'utf8') });
}
const file_name = `demo-${cfg.run_id}.zip`;
const key = `${cfg.prefix}${file_name}`;
const manifest = {
  format: 'automation-lab/db-dump-json/v1',
  database: meta.database, schema: cfg.schema, server_version: meta.server_version,
  run_id: cfg.run_id, dumped_at, file_name, key, bucket: cfg.bucket,
  tables: manifestTables, table_count: tables.length, total_rows, bytes_uncompressed,
  restore: "unzip, then per table: insert into <table> select * from json_populate_recordset(null::<table>, '<rows json>')",
};
const mtext = JSON.stringify(manifest, null, 2);
binary.manifest = await this.helpers.prepareBinaryData(Buffer.from(mtext, 'utf8'), 'manifest.json', 'application/json');
props.push('manifest');
bytes_uncompressed += Buffer.byteLength(mtext, 'utf8');
return [{ json: { run_id: cfg.run_id, database: meta.database, server_version: meta.server_version, dumped_at,
                  file_name, key, bucket: cfg.bucket, table_count: tables.length, total_rows, bytes_uncompressed,
                  tables: manifestTables, binary_props: props.join(',') },
          binary }];
"""

PLAN_JS = r"""
// Input: every object under the prefix (or one empty item when the bucket has none). Keep the newest `keep`
// dumps, expire the rest. Only keys that look like our own dumps are touched (regex), and the object uploaded a
// moment ago must be in the listing - otherwise the upload did not land and this run must fail loudly.
const cfg = $('Config').first().json;
const built = $('Build dump files').first().json;
const keep = Math.max(1, Math.floor(Number(cfg.keep) || 7));
const re = new RegExp('^' + cfg.prefix.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + 'demo-\\d{8}-\\d{6}\\.zip$');
const objects = $input.all().map(i => i.json).filter(o => o && typeof o.Key === 'string' && re.test(o.Key));
if (!objects.some(o => o.Key === built.key)) {
  throw new Error(`D05: uploaded dump ${built.key} is not in the s3://${cfg.bucket}/${cfg.prefix} listing`);
}
// run_id is yyyyLLdd-HHmmss, so the key sorts chronologically; newest first.
objects.sort((a, b) => b.Key.localeCompare(a.Key));
const kept = objects.slice(0, keep);
const expired = objects.slice(keep).map(o => ({ Key: o.Key, Size: Number(o.Size) || 0, LastModified: o.LastModified }));
const current = objects.find(o => o.Key === built.key) || {};
return [{ json: {
  run_id: cfg.run_id, key: built.key, keep,
  found: objects.length, kept_count: kept.length, expired_count: expired.length,
  kept: kept.map(o => o.Key), expired,
  dump_bytes: Number(current.Size) || 0, dump_etag: current.ETag || null,
} }];
"""

SUMMARY_JS = r"""
// Runs once after the deletes (or straight from the If when there was nothing to delete).
const cfg = $('Config').first().json;
const built = $('Build dump files').first().json;
const plan = $('Plan rotation').first().json;
const finished_at = new Date().toISOString();
const meta = {
  run_id: cfg.run_id, database: built.database, server_version: built.server_version,
  bucket: cfg.bucket, key: built.key, dump_bytes: plan.dump_bytes, bytes_uncompressed: built.bytes_uncompressed,
  table_count: built.table_count, total_rows: built.total_rows,
  tables: Object.fromEntries(built.tables.map(t => [t.table, t.row_count])),
  keep: plan.keep, found: plan.found, kept: plan.kept, deleted: plan.expired.map(e => e.Key),
  dumped_at: built.dumped_at, finished_at,
};
const notes = `dumped ${built.table_count} tables / ${built.total_rows} rows to s3://${cfg.bucket}/${built.key} ` +
              `(${plan.dump_bytes} bytes zip); kept ${plan.kept_count}, deleted ${plan.expired_count} older dump(s)`;
return [{ json: { kind: 'db-dump', file_name: built.file_name, storage_key: `s3://${cfg.bucket}/${built.key}`,
                  meta, notes, table_count: built.table_count, total_rows: built.total_rows,
                  kept: plan.kept_count, deleted: plan.expired_count } }];
"""


def build() -> Workflow:
    wf = Workflow("D05", "db-dump-to-minio", "Scheduled DB Dump to MinIO with Rotation", tags=["Data & ETL"],
                  error_workflow=catalog_id("P01"),
                  description="Nightly logical dump of the demo database (JSON per table, zipped) to MinIO "
                              "artifacts/db-dumps/, keeping the newest 7 dumps; documents row + P08 log per run.")
    trg = schedule(wf, "Daily at 02:00", daily_at=(2, 0))
    manual = manual_trigger(wf, "Run once (manual / CLI)")
    cfg = set_fields(wf, "Config", {
        "started_at": "={{ $now.toISO() }}",
        "run_id": "={{ $now.toFormat('yyyyLLdd-HHmmss') }}",
        "schema": "public",
        "bucket": "artifacts",
        "prefix": "db-dumps/",
        "keep": 7,
    })
    cfg.note("keep = how many dumps survive rotation (PRD: 7). prefix must end with '/'.")

    tables = postgres_query(wf, "List tables", LIST_TABLES_SQL,
                            params="={{ $('Config').first().json.schema }}").retry(3, 1000)
    tables.note("information_schema.tables: one item per base table (quote_ident form for the FROM clause).")
    dump = postgres_query(wf, "Dump table (json_agg)", DUMP_TABLE_SQL, params="={{ $json.table_name }}").retry(3, 1000)
    dump.note("One SELECT per table; Postgres serialises the rows to JSON (timestamps ISO, numerics as numbers, jsonb nested).")
    files = code(wf, "Build dump files", BUILD_FILES_JS)
    zipped = compress(wf, "Zip dump", "={{ $json.file_name }}", prop="={{ $json.binary_props }}")
    zipped.note("One zip: <table>.json per table + manifest.json (row counts, server version, restore hint).")
    upload = s3_upload(wf, "Upload to MinIO (artifacts)", "artifacts",
                       "={{ $('Build dump files').first().json.key }}").retry(3, 1000)
    listing = s3_list(wf, "List dumps (MinIO)", "artifacts", prefix="db-dumps/").retry(3, 1000).always_output()
    plan = code(wf, "Plan rotation", PLAN_JS)
    any_del = if_(wf, "Anything to delete?", [cond_num("={{ $json.expired_count }}", "gt", 0)])
    each = split_out(wf, "One item per expired dump", "expired")
    delete = s3_delete(wf, "Delete expired dump", "artifacts", "={{ $json.Key }}").retry(3, 1000)
    summary = code(wf, "Summarize run", SUMMARY_JS)
    doc = postgres_insert(wf, "Record dump (documents)", "documents", {
        "kind": "={{ $json.kind }}",
        "file_name": "={{ $json.file_name }}",
        "storage_key": "={{ $json.storage_key }}",
        "meta": "={{ JSON.stringify($json.meta) }}",
    }, returning=True).retry(3, 1000)
    log_in = set_fields(wf, "Log input", {
        "execution_id": "={{ $execution.id }}",
        "workflow_id": "={{ $workflow.id }}",
        "workflow_name": "={{ $workflow.name }}",
        "status": "success",
        "started_at": "={{ $('Config').first().json.started_at }}",
        "notes": "={{ $('Summarize run').first().json.notes }}",
    })
    log = execute_workflow(wf, "Log execution (P08)", catalog_id("P08"), cached_name="P08 - Log execution")

    wf.chain(trg, cfg)
    wf.connect(manual, cfg)
    wf.chain(cfg, tables, dump, files, zipped, upload, listing, plan, any_del)
    wf.connect(any_del, each, out=0)
    wf.chain(each, delete, summary)
    wf.connect(any_del, summary, out=1)
    wf.chain(summary, doc, log_in, log)
    wf.sticky(
        "## D05 - Scheduled DB dump -> MinIO, keep the newest 7\n"
        "No pg_dump here (Execute Command stays disabled): `information_schema` lists the base tables, one "
        "`SELECT count(*), json_agg(row_to_json(t))` per table lets Postgres serialise the rows, a Code node turns "
        "them into `<table>.json` + `manifest.json`, Compression zips them, S3 uploads "
        "`artifacts/db-dumps/demo-<yyyyLLdd-HHmmss>.zip`.\n\n"
        "Rotation: list the prefix, sort by key (the run id is a timestamp), keep `Config.keep` (7), delete the "
        "rest one S3 delete per key. The fresh upload must appear in the listing or the run fails (**P01**). "
        "`documents` row (kind `db-dump`, meta = row counts, kept/deleted keys) and a **P08** `execution_log` row "
        "close the run. Restore with psql: `json_populate_recordset(null::<table>, rows)` (test/restore-table.py).",
        pos=(-40, -360), width=760, height=280)
    return wf


if __name__ == "__main__":
    build().save()
