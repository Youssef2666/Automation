#!/usr/bin/env python
"""T03 - Polling an API without webhooks.

Schedule (1 min) -> Redis GET cursor -> P02 HTTP with backoff (GET /events?id_gte=cursor+1, 50 per run)
-> insert each event into webhook_events (unique external_id, duplicate-safe) -> Redis SET cursor = max id
-> upsert sync_state. No duplicate reads: the cursor moves only after the batch is stored.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, aggregate, catalog_id, code, cond_bool, execute_workflow, if_,  # noqa: E402
                         manual_trigger, noop, postgres_insert, postgres_upsert, redis, schedule, set_fields,
                         split_out, stop_error)

SUMMARY_JS = r"""
// One aggregated item in. Compute the new cursor from the events fetched in this run.
const events = $('Split events').all().map(i => i.json);
const inserted = $input.first().json.data || [];
const ids = events.map(e => Number(e.id)).filter(n => Number.isFinite(n));
const prev = Number($('Read cursor').first().json.cursor || 0);
return [{ json: {
  source: 'mock-api-events',
  previous_cursor: prev,
  cursor: ids.length ? Math.max(...ids) : prev,
  fetched: events.length,
  stored: inserted.filter(r => r && r.id !== undefined).length,
  last_run_at: new Date().toISOString(),
}}];
"""


def build() -> Workflow:
    wf = Workflow("T03", "api-polling", "Polling an API Without Webhooks", tags=["Triggers"],
                  error_workflow=catalog_id("P01"),
                  description="Cursor-based polling of a REST feed with backoff and duplicate-safe inserts.")
    trg = schedule(wf, "Every minute", minutes=1)
    manual = manual_trigger(wf, "Run once (manual / CLI)")   # `n8n execute` needs a manual trigger
    cur = redis(wf, "Read cursor", "get", "t03:events:cursor", prop="cursor").always_output()
    poll_in = set_fields(wf, "Poll request", {
        "url": "http://mock-api:8080/events",
        "query": ("={{ { id_gte: Number($json.cursor || 0) + 1, _sort: 'id', _order: 'asc', _limit: 50 } }}", "object"),
        "max_attempts": 5,
        "base_ms": 500,
    })
    fetch = execute_workflow(wf, "HTTP with backoff (P02)", catalog_id("P02"), cached_name="P02 - HTTP with backoff")
    ok = if_(wf, "Fetched OK?", [cond_bool("={{ $json.ok }}")])
    fail = stop_error(wf, "Poll failed", "=T03 poll failed after {{ $json.attempts }} attempt(s): {{ $json.response }}")
    has = if_(wf, "Any new events?", [cond_bool("={{ Array.isArray($json.data) && $json.data.length > 0 }}")])
    none = noop(wf, "Nothing new")
    split = split_out(wf, "Split events", "data")
    ins = postgres_insert(wf, "Insert webhook_events", "webhook_events", {
        "external_id": "=mock-event-{{ $json.id }}",
        "source": "mock-api-poll",
        "event_type": "={{ $json.type }}",
        "payload": "={{ JSON.stringify($json) }}",
        "received_at": "={{ $now.toISO() }}",
    }, returning=True).on_error("continueRegularOutput").always_output()
    ins.note("Unique external_id: a replayed page is rejected row by row, not as a batch.")
    agg = aggregate(wf, "Collect inserted")
    summary = code(wf, "Summarize run", SUMMARY_JS)
    setcur = redis(wf, "Advance cursor", "set", "t03:events:cursor", "={{ String($json.cursor) }}").retry(3, 500)
    state = postgres_upsert(wf, "Upsert sync_state", "sync_state", ["source"], {
        "source": "={{ $('Summarize run').item.json.source }}",
        "cursor": "={{ String($('Summarize run').item.json.cursor) }}",
        "last_run_at": "={{ $('Summarize run').item.json.last_run_at }}",
        "rows_seen": "={{ $('Summarize run').item.json.fetched }}",
    })
    wf.chain(trg, cur, poll_in, fetch, ok)
    wf.connect(manual, cur)
    wf.connect(ok, has, out=0)
    wf.connect(ok, fail, out=1)
    wf.connect(has, split, out=0)
    wf.connect(has, none, out=1)
    wf.chain(split, ins, agg, summary, setcur, state)
    wf.sticky(
        "## T03 - Polling without webhooks\n"
        "Cursor in Redis (`t03:events:cursor`), 50 events per run, ascending ids.\n\n"
        "- fetch goes through **P02** (backoff on 5xx/429/network errors)\n"
        "- inserts are duplicate-safe (`webhook_events.external_id` UNIQUE, continue on error)\n"
        "- cursor advances **after** the batch is stored; `sync_state` keeps an audit row\n\n"
        "Reset: `redis-cli del t03:events:cursor`.",
        pos=(-40, -330), width=600, height=240)
    return wf


if __name__ == "__main__":
    build().save()
