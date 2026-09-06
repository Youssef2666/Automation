#!/usr/bin/env python
"""O05 - Execution Logs to Postgres to Metabase.

Every 15 min (or manual) -> ensure the unique index on execution_log(execution_id) -> Redis GET o05:last_sync
-> n8n public API: executions (newest 250, no data) + workflows (id -> name) -> Code: finished executions started
after the cursor, mapped to execution_log fields -> one bulk INSERT ... ON CONFLICT (fills missing timing on rows
P08/P01 wrote, never overrides status) -> Redis SET o05:last_sync -> 24 h stats (runs per workflow/status, p50/p95)
-> text summary -> notifications row (channel log, target metabase) -> P08 logs this run.

The cursor is a hint (it stays behind the oldest still-running execution); the unique index is the guarantee.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, catalog_id, code, execute_workflow, manual_trigger, n8n_api, postgres_insert,  # noqa: E402
                         postgres_query, redis, schedule, set_fields)

ENSURE_INDEX_SQL = """create unique index if not exists execution_log_execution_id_uidx
  on execution_log (execution_id)
  where execution_id is not null"""

SELECT_JS = r"""
// Inputs: the workflow list (this node's input), the executions list, the Redis cursor.
const names = {};
for (const w of $input.all()) if (w.json && w.json.id) names[w.json.id] = w.json.name;
const executions = $('Fetch executions').all().map(i => i.json).filter(e => e && e.id);
const cursorRaw = $('Read last sync').first().json.last_sync;
const cursor = cursorRaw ? new Date(cursorRaw) : new Date('1970-01-01T00:00:00Z');
const started_at_run = $('Config').first().json.started_at;

// n8n status -> our vocabulary. Unfinished runs are skipped and pull the cursor back so the next run sees them.
const STATUS = { success: 'success', error: 'error', crashed: 'error', canceled: 'warning' };
const rows = [];
let maxStarted = null;
let oldestUnfinished = null;
for (const e of executions) {
  const started = e.startedAt ? new Date(e.startedAt) : null;
  if (!started || Number.isNaN(started.getTime())) continue;
  const status = STATUS[e.status];
  if (!status || !e.stoppedAt) {                     // running / waiting / new: not final yet
    if (!oldestUnfinished || started < oldestUnfinished) oldestUnfinished = started;
    continue;
  }
  if (started <= cursor) continue;
  if (!maxStarted || started > maxStarted) maxStarted = started;
  const stopped = new Date(e.stoppedAt);
  rows.push({
    execution_id: String(e.id),
    workflow_id: String(e.workflowId || ''),
    workflow_name: names[e.workflowId] || String(e.workflowId || 'unknown'),
    status,
    started_at: started.toISOString(),
    finished_at: stopped.toISOString(),
    duration_ms: Math.max(0, stopped.getTime() - started.getTime()),
    mode: e.mode || null,
  });
}
// Next cursor: just before the oldest unfinished execution (so it is picked up once it ends), else the newest seen.
let next = maxStarted || cursor;
if (oldestUnfinished && oldestUnfinished.getTime() - 1 < next.getTime()) next = new Date(oldestUnfinished.getTime() - 1);
return [{ json: {
  last_sync: cursor.toISOString(),
  next_sync: next.toISOString(),
  fetched: executions.length,
  candidates: rows.length,
  rows_json: JSON.stringify(rows),
  started_at: started_at_run,
} }];
"""

INSERT_SQL = """with src as (
  select * from jsonb_to_recordset($1::jsonb)
    as r(execution_id text, workflow_id text, workflow_name text, status text,
         started_at timestamptz, finished_at timestamptz, duration_ms int)
), up as (
  insert into execution_log (execution_id, workflow_id, workflow_name, status, started_at, finished_at, duration_ms)
  select execution_id, workflow_id, workflow_name, status, started_at, finished_at, duration_ms from src
  on conflict (execution_id) where execution_id is not null do update
     set started_at  = coalesce(execution_log.started_at, excluded.started_at),
         finished_at = coalesce(execution_log.finished_at, excluded.finished_at),
         duration_ms = coalesce(execution_log.duration_ms, excluded.duration_ms)
   where execution_log.started_at is null or execution_log.finished_at is null or execution_log.duration_ms is null
  returning (xmax = 0) as inserted
)
select count(*) filter (where inserted)::int      as inserted,
       count(*) filter (where not inserted)::int  as enriched,
       (select count(*) from src)::int            as candidates
  from up"""

STATS_SQL = """select workflow_name, status, count(*)::int as runs,
       round(percentile_cont(0.5) within group (order by duration_ms))::int as p50_ms,
       round(percentile_cont(0.95) within group (order by duration_ms))::int as p95_ms,
       max(finished_at) as last_run
from execution_log
where coalesce(started_at, logged_at) > now() - interval '24 hours'
group by workflow_name, status
order by workflow_name, status"""

SUMMARY_JS = r"""
const sel = $('Select new executions').first().json;
const ins = $('Insert new rows').first().json;
const stats = $input.all().map(i => i.json).filter(r => r.workflow_name);
const pad = (s, n) => String(s ?? '').padEnd(n).slice(0, n);
const lines = [
  `O05 sync ${new Date().toISOString()}: fetched ${sel.fetched} executions, ${sel.candidates} after cursor ` +
  `(${sel.last_sync}), inserted ${ins.inserted ?? 0}, enriched ${ins.enriched ?? 0}; next cursor ${sel.next_sync}`,
  '',
  'Last 24 h per workflow / status (runs, p50 ms, p95 ms):',
];
if (!stats.length) lines.push('  (no rows in the last 24 h)');
for (const r of stats) {
  lines.push(`  ${pad(r.workflow_name, 48)} ${pad(r.status, 8)} ${String(r.runs).padStart(4)} ` +
             `${String(r.p50_ms ?? '-').padStart(7)} ${String(r.p95_ms ?? '-').padStart(7)}`);
}
const errors = stats.filter(r => r.status === 'error').reduce((a, r) => a + r.runs, 0);
const total = stats.reduce((a, r) => a + r.runs, 0);
lines.push('', `${total} runs in 24 h, ${errors} errors. Dashboard: Metabase on port 3001 (compose profile observability).`);
return [{ json: {
  subject: `[Automation Lab] O05 execution log sync: +${ins.inserted ?? 0} rows, ${total} runs / ${errors} errors in 24 h`,
  body: lines.join('\n'),
  inserted: ins.inserted ?? 0, enriched: ins.enriched ?? 0, candidates: sel.candidates, fetched: sel.fetched,
  runs_24h: total, errors_24h: errors, next_sync: sel.next_sync, started_at: sel.started_at,
} }];
"""


def build() -> Workflow:
    wf = Workflow("O05", "execution-logs-metabase", "Execution Logs to Postgres to Metabase", tags=["DevOps"],
                  error_workflow=catalog_id("P01"),
                  description="Backfills demo.execution_log from the n8n public API every 15 minutes and writes a "
                              "24 h stats summary; Metabase charts the table.")
    trg = schedule(wf, "Every 15 minutes", minutes=15)
    manual = manual_trigger(wf, "Run once (manual / CLI)")
    cfg = set_fields(wf, "Config", {"started_at": "={{ $now.toISO() }}", "cursor_key": "o05:last_sync"})
    ensure = postgres_query(wf, "Ensure unique index", ENSURE_INDEX_SQL).once().always_output().retry(3, 1000)
    ensure.note("create unique index if not exists execution_log_execution_id_uidx (idempotent DDL)")
    cursor = redis(wf, "Read last sync", "get", "={{ $('Config').item.json.cursor_key }}", prop="last_sync").retry(3, 500)
    fetch = n8n_api(wf, "Fetch executions", "execution", "getAll", limit_=250).retry(3, 2000)
    fetch.parameters["options"] = {"activeWorkflows": False}      # "Include Execution Details" = includeData=false
    fetch.always_output()
    fetch.note("Public API GET /executions (newest 250, no node data). Needs credential 'n8n API - local'.")
    wfs = n8n_api(wf, "Fetch workflow names", "workflow", "getAll", return_all=True,
                  filters={"excludePinnedData": True}).once().always_output().retry(3, 2000)
    select = code(wf, "Select new executions", SELECT_JS)
    insert = postgres_query(wf, "Insert new rows", INSERT_SQL, params="={{ [ $json.rows_json ] }}").retry(3, 1000)
    insert.note("Bulk INSERT ... ON CONFLICT (execution_id): new rows inserted, rows P08/P01 wrote only get missing timing filled.")
    # .first() from here on: "Select new executions" emits a new item, so paired-item lookups stop at it.
    save = redis(wf, "Save last sync", "set", "={{ $('Config').first().json.cursor_key }}",
                 value="={{ $('Select new executions').first().json.next_sync }}").retry(3, 500)
    stats = postgres_query(wf, "Stats last 24 h", STATS_SQL).once().always_output().retry(3, 1000)
    render = code(wf, "Render summary", SUMMARY_JS)
    note = postgres_insert(wf, "Record notification", "notifications", {
        "channel": "log",
        "target": "metabase",
        "subject": "={{ $json.subject }}",
        "body": "={{ $json.body }}",
        "severity": "info",
        "sent_at": "={{ $now.toISO() }}",
    }).on_error("continueRegularOutput")
    log_in = set_fields(wf, "Log input", {
        "execution_id": "={{ $execution.id }}",
        "workflow_id": "={{ $workflow.id }}",
        "workflow_name": "={{ $workflow.name }}",
        "status": "success",
        "started_at": "={{ $('Config').first().json.started_at }}",
        "notes": "={{ 'synced ' + $('Render summary').first().json.inserted + ' new executions (' + $('Render summary').first().json.candidates + ' candidates), ' + $('Render summary').first().json.runs_24h + ' runs / ' + $('Render summary').first().json.errors_24h + ' errors in 24 h' }}",
    })
    log = execute_workflow(wf, "Log execution (P08)", catalog_id("P08"), cached_name="P08 - Log execution")
    wf.chain(trg, cfg, ensure, cursor, fetch, wfs, select, insert, save, stats, render, note, log_in, log)
    wf.connect(manual, cfg)
    wf.sticky(
        "## O05 - Execution logs -> Postgres -> Metabase\n"
        "Every 15 min: Redis cursor `o05:last_sync` -> n8n public API (`n8n API - local`) -> new finished executions "
        "-> `execution_log` (`ON CONFLICT` on the unique index this workflow creates) -> cursor -> 24 h stats "
        "(p50/p95) -> `notifications` (channel `log`) -> P08 logs this run.\n\n"
        "Status map: success->success, error/crashed->error, canceled->warning, running/waiting skipped. "
        "Dashboard: `docker compose --profile observability up -d`, http://localhost:3001 (see README + "
        "test/metabase-queries.sql).",
        pos=(-40, -330), width=720, height=250)
    return wf


if __name__ == "__main__":
    build().save()
