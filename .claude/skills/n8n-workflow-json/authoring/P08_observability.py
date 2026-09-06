#!/usr/bin/env python
"""P08 - Log execution (sub-workflow) + test/harness.json.

Input (one item): {workflow_id, status ('success'|'error'|'warning'|'info'), execution_id?, workflow_name?, started_at?,
                   finished_at?, duration_ms?, error_message?, error_node?, notes?}
Output (one flat item): {ok, logged, action ('inserted'|'updated'|'failed'), row_id, execution_id, workflow_id,
                         workflow_name, status, duration_ms, response}

One row per execution in demo.execution_log: a second call with the same execution_id updates the row (last call
wins), so a workflow can log `info` early and `success` at the end. The write is a writable-CTE upsert, so it does
not need the unique index that O05 adds later for its bulk backfill. Logging never fails the caller: the Postgres
node continues on error and the result says `logged: false`.

Callers (see README): Set node with execution_id "={{ $execution.id }}", workflow_id "={{ $workflow.id }}",
workflow_name "={{ $workflow.name }}" -> execute_workflow(wf, "Log execution (P08)", catalog_id("P08")).
Inside a sub-workflow $execution / $workflow refer to the sub-workflow itself, hence the caller passes them.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, catalog_id, code, cond_not_empty, cond_regex, execute_workflow, if_,  # noqa: E402
                         manual_trigger, postgres_query, set_fields, stop_error, sub_trigger, wf_id)

P08_ID = catalog_id("P08")
STATUSES = "success|error|warning|info"

NORMALIZE_JS = r"""
// One call = one item. Every optional field becomes null (never undefined) so the SQL casts stay clean.
const src = $json;
const str = (v, max) => (v === undefined || v === null || v === '') ? null : String(v).slice(0, max || 4000);
const iso = (v) => {
  if (v === undefined || v === null || v === '') return null;
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
};
const started = iso(src.started_at);
const finished = iso(src.finished_at) || new Date().toISOString();   // the log call is the end of the run
let duration = (src.duration_ms === undefined || src.duration_ms === null || src.duration_ms === '')
  ? null : Math.round(Number(src.duration_ms));
if (duration === null && started) duration = Math.max(0, new Date(finished).getTime() - new Date(started).getTime());
if (duration !== null && !Number.isFinite(duration)) duration = null;
const workflow_id = String(src.workflow_id).trim();
return { json: {
  execution_id: str(src.execution_id, 64),
  workflow_id,
  workflow_name: str(src.workflow_name, 200) || workflow_id,
  status: String(src.status).trim().toLowerCase(),
  started_at: started,
  finished_at: finished,
  duration_ms: duration,
  // execution_log has no notes column: notes go to error_message when there is no error message (documented).
  error_message: str(src.error_message, 500) || str(src.notes, 500),
  error_node: str(src.error_node, 200),
  notes: str(src.notes, 500),
} };
"""

UPSERT_SQL = """with input as (
  select $1::text as execution_id, $2::text as workflow_id, $3::text as workflow_name, $4::text as status,
         $5::timestamptz as started_at, $6::timestamptz as finished_at, $7::int as duration_ms,
         $8::text as error_message, $9::text as error_node
), updated as (
  update execution_log l
     set workflow_name = i.workflow_name,
         status        = i.status,
         started_at    = coalesce(i.started_at, l.started_at),
         finished_at   = coalesce(i.finished_at, l.finished_at),
         duration_ms   = coalesce(i.duration_ms, l.duration_ms),
         error_message = coalesce(i.error_message, l.error_message),
         error_node    = coalesce(i.error_node, l.error_node),
         logged_at     = now()
    from input i
   where i.execution_id is not null and l.execution_id = i.execution_id
  returning l.id, 'updated'::text as action
), inserted as (
  insert into execution_log (execution_id, workflow_id, workflow_name, status, started_at, finished_at, duration_ms,
                             error_message, error_node)
  select execution_id, workflow_id, workflow_name, status, started_at, finished_at, duration_ms, error_message, error_node
    from input
   where not exists (select 1 from updated)
  returning id, 'inserted'::text as action
)
select id, action from updated
union all
select id, action from inserted"""

UPSERT_PARAMS = ("={{ [ $json.execution_id, $json.workflow_id, $json.workflow_name, $json.status, $json.started_at, "
                 "$json.finished_at, $json.duration_ms, $json.error_message, $json.error_node ] }}")

RESULT_JS = r"""
// Runs per item so it pairs with the normalised input even when the Postgres node continued on error.
const input = $('Normalize input').item.json;
const out = $json;
const failed = out.action === undefined;          // continueRegularOutput emits {message, error} instead of a row
const response = failed
  ? `execution_log write failed: ${out.message || (out.error && out.error.message) || 'unknown error'}`
  : `${out.action} execution_log row ${out.id}: ${input.status} for ${input.workflow_name}`;
return { json: {
  ok: !failed,
  logged: !failed,
  action: failed ? 'failed' : out.action,
  row_id: failed ? null : out.id,
  execution_id: input.execution_id,
  workflow_id: input.workflow_id,
  workflow_name: input.workflow_name,
  status: input.status,
  duration_ms: input.duration_ms,
  response,
} };
"""


def build() -> Workflow:
    wf = Workflow("P08", "observability", "Log execution", tags=["pattern", "P08"],
                  error_workflow=catalog_id("P01"), caller_policy="workflowsFromSameOwner",
                  description="Writes one structured execution_log row per run (upsert by execution_id); "
                              "never fails the caller.")
    trg = sub_trigger(wf)
    valid = if_(wf, "Has workflow_id and status?", [
        cond_not_empty("={{ String($json.workflow_id ?? '') }}"),
        cond_regex("={{ String($json.status ?? '') }}", f"^({STATUSES})$"),
    ])
    bad = stop_error(wf, "Missing workflow_id or status",
                     f"P08 log execution: input item needs workflow_id and status in ({STATUSES})")
    norm = code(wf, "Normalize input", NORMALIZE_JS, per_item=True)
    write = postgres_query(wf, "Upsert execution_log", UPSERT_SQL, params=UPSERT_PARAMS) \
        .retry(3, 1000).on_error("continueRegularOutput")
    write.note("One row per execution_id (update if it exists). Continue on error: logging never fails the caller.")
    result = code(wf, "Result", RESULT_JS, per_item=True)
    wf.chain(trg, valid)
    wf.connect(valid, norm, out=0)
    wf.connect(valid, bad, out=1)
    wf.chain(norm, write, result)
    wf.sticky(
        "## P08 - Log execution\n"
        "Call at the **end of the happy path** with `{execution_id, workflow_id, workflow_name, status, started_at?, "
        "notes?}`; P01 logs the failures.\n\n"
        "Status vocabulary: `success` | `warning` (ran, something worth a look) | `info` (ran, nothing to do) | "
        "`error` (P01 / manual).\n\n"
        "Upsert by `execution_id` (last call wins) -> `{ok, logged, action, row_id, response}`. "
        "Pass `$execution.id` / `$workflow.id` from the caller: inside this sub-workflow they refer to P08 itself.",
        pos=(-40, -330), width=640, height=250)
    return wf


def build_harness() -> Workflow:
    """Manual workflow that calls P08 twice: a success row and a warning row (the second call uses a suffixed
    execution_id so the harness produces two rows from one manual run; a real caller logs once per run)."""
    wf = Workflow("P08", "harness", "P08 harness (test)", tags=["test"], error_workflow=catalog_id("P01"),
                  description="Calls P08 - Log execution twice (success + warning). Test fixture only.")
    trg = manual_trigger(wf, "Run once (manual / CLI)")
    started = set_fields(wf, "Started", {"started_at": "={{ $now.toISO() }}"})
    ok_in = set_fields(wf, "Simulate success", {
        "execution_id": "={{ $execution.id }}",
        "workflow_id": "={{ $workflow.id }}",
        "workflow_name": "={{ $workflow.name }}",
        "status": "success",
        "started_at": "={{ $('Started').item.json.started_at }}",
        "notes": "harness: 5 of 5 rows processed",
    })
    log_ok = execute_workflow(wf, "Log success (P08)", P08_ID, cached_name="P08 - Log execution")
    warn_in = set_fields(wf, "Simulate warning", {
        "execution_id": "={{ $execution.id }}-warn",
        "workflow_id": "={{ $workflow.id }}",
        "workflow_name": "={{ $workflow.name }}",
        "status": "warning",
        "started_at": "={{ $('Started').item.json.started_at }}",
        "notes": "harness: 2 of 5 rows skipped (simulated partial failure)",
    })
    log_warn = execute_workflow(wf, "Log warning (P08)", P08_ID, cached_name="P08 - Log execution")
    summary = code(wf, "Harness result", r"""
const a = $('Log success (P08)').first().json;
const b = $input.first().json;
return [{ json: { success_call: a, warning_call: b, both_logged: Boolean(a.logged && b.logged) } }];
""")
    wf.chain(trg, started, ok_in, log_ok, warn_in, log_warn, summary)
    wf.sticky("## P08 harness\nRuns P08 twice from one manual execution: `success` (execution_id = $execution.id) "
              "and `warning` (execution_id suffixed `-warn` so it lands in a second row). "
              "Check `select * from execution_log order by id desc limit 2`.",
              pos=(-40, -260), width=560, height=170)
    return wf


if __name__ == "__main__":
    sub = build()
    sub.save()
    build_harness().save(sub.folder() / "test" / "harness.json")
    print("P08 id:", P08_ID, "harness id:", wf_id("P08", "harness"))
