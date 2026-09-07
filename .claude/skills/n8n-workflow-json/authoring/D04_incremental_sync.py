#!/usr/bin/env python
"""D04 - Incremental sync with upsert + dedupe (change detection via hash).

Schedule (15 min) -> read watermark from sync_state (source `mock-api-products`) -> P02 GET
/products?updated_at_gte=<cursor> -> one item per row -> canonical JSON of the business fields -> SHA-256
(Crypto node) -> `INSERT ... ON CONFLICT (id) DO UPDATE ... WHERE content_hash IS DISTINCT FROM EXCLUDED.content_hash
RETURNING (xmax = 0) AS inserted` into products_mirror -> summary {fetched, inserted, updated, skipped} ->
advance the watermark (cursor = max updated_at seen) only after the writes succeeded.

Second run with an unchanged source: the same rows come back (>= watermark), every hash matches, zero writes,
watermark unchanged - a no-op that still records last_run_at. A touched row whose content did not change is
skipped as well: the hash covers sku/name/price/stock, not updated_at.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, aggregate, catalog_id, code, cond_bool, crypto_hash, execute_workflow,  # noqa: E402
                         if_, manual_trigger, noop, postgres_query, postgres_upsert, schedule, set_fields,
                         split_out, stop_error)

EPOCH = "1970-01-01T00:00:00Z"

UPSERT_SQL = """INSERT INTO products_mirror (id, sku, name, price, stock, content_hash, synced_at)
VALUES ($1::int, $2, $3, $4::numeric, $5::int, $6, now())
ON CONFLICT (id) DO UPDATE
   SET sku = EXCLUDED.sku, name = EXCLUDED.name, price = EXCLUDED.price, stock = EXCLUDED.stock,
       content_hash = EXCLUDED.content_hash, synced_at = now()
 WHERE products_mirror.content_hash IS DISTINCT FROM EXCLUDED.content_hash
RETURNING id, sku, content_hash, (xmax = 0) AS inserted"""

SUMMARY_JS = r"""
// One aggregated item in: the RETURNING rows of "Upsert if changed" (one per inserted/updated row; skipped rows
// return nothing). On the empty-batch path the aggregate wraps the P02 result instead - filtered out below.
const cfg = $('Config').first().json;
const fetched = $('Fetch source (P02)').first().json.data || [];        // every row the API returned this run
const results = ($input.first().json.data || []).filter(r => r && r.id !== undefined && r.inserted !== undefined);
const inserted = results.filter(r => r.inserted === true).length;
const prev = String($('Read watermark').first().json.cursor || cfg.epoch);
const newest = fetched.map(r => String(r.updated_at || '')).filter(Boolean).sort().pop() || prev;
return [{ json: {
  source: cfg.source,
  previous_cursor: prev,
  cursor: newest > prev ? newest : prev,       // ISO-8601 Z strings compare lexicographically
  fetched: fetched.length,
  inserted,
  updated: results.length - inserted,
  skipped: fetched.length - results.length,    // hash equal -> no write
  changed_ids: results.map(r => r.id),
  last_run_at: new Date().toISOString(),
} }];
"""


def build() -> Workflow:
    wf = Workflow("D04", "incremental-sync", "Incremental Sync with Upsert and Dedupe", tags=["Data & ETL"],
                  error_workflow=catalog_id("P01"),
                  description="Pulls /products since the last watermark, hashes each row, upserts only changed "
                              "rows into products_mirror and advances the watermark in sync_state.")
    trg = schedule(wf, "Every 15 minutes", minutes=15)
    manual = manual_trigger(wf, "Run once (manual / CLI)")   # `n8n execute` needs a manual trigger
    cfg = set_fields(wf, "Config", {
        "source": "mock-api-products",            # sync_state.source (primary key of the watermark row)
        "url": "http://mock-api:8080/products",
        "page_limit": 500,
        "epoch": EPOCH,                           # cursor used when sync_state has no row yet
    })
    wm = postgres_query(wf, "Read watermark",
                        "SELECT cursor, rows_seen, last_run_at FROM sync_state WHERE source = $1",
                        params="={{ $json.source }}").retry(3, 1000).always_output()
    wm.note("No row yet -> empty item -> epoch cursor (first run pulls everything).")
    req = set_fields(wf, "Source request", {
        "url": "={{ $('Config').first().json.url }}",
        "query": ("={{ { updated_at_gte: $json.cursor || $('Config').first().json.epoch, _sort: 'updated_at', "
                  "_order: 'asc', _limit: $('Config').first().json.page_limit } }}", "object"),
        "max_attempts": 5,
        "base_ms": 500,
    })
    fetch = execute_workflow(wf, "Fetch source (P02)", catalog_id("P02"), cached_name="P02 - HTTP with backoff")
    ok = if_(wf, "Fetched OK?", [cond_bool("={{ $json.ok }}")])
    fail = stop_error(wf, "Source fetch failed",
                      "=D04 /products failed after {{ $json.attempts }} attempt(s): {{ $json.response }}")
    has = if_(wf, "Any rows?", [cond_bool("={{ Array.isArray($json.data) && $json.data.length > 0 }}")])
    none = noop(wf, "Nothing fetched")
    none.note("Empty batch: still record last_run_at, cursor unchanged.")
    split = split_out(wf, "Split rows", "data")
    canon = set_fields(wf, "Canonical row", {
        "id": ("={{ Number($json.id) }}", "number"),
        "sku": "={{ String($json.sku || '').trim().toUpperCase() }}",
        "name": "={{ String($json.name || '').trim() }}",
        "price": ("={{ Math.round(Number($json.price) * 100) / 100 }}", "number"),
        "stock": ("={{ Math.max(0, parseInt($json.stock, 10) || 0) }}", "number"),
        "updated_at": "={{ $json.updated_at }}",
        "canonical": ("={{ JSON.stringify({ sku: String($json.sku || '').trim().toUpperCase(), "
                      "name: String($json.name || '').trim(), price: Math.round(Number($json.price) * 100) / 100, "
                      "stock: Math.max(0, parseInt($json.stock, 10) || 0) }) }}"),
    })
    canon.note("Business fields only, fixed key order. updated_at is NOT part of the hash.")
    hsh = crypto_hash(wf, "Hash row (SHA-256)", "={{ $json.canonical }}", prop="content_hash")
    up = postgres_query(wf, "Upsert if changed", UPSERT_SQL,
                        params="={{ $json.id }}, {{ $json.sku }}, {{ $json.name }}, {{ $json.price }}, "
                               "{{ $json.stock }}, {{ $json.content_hash }}").retry(3, 1000).always_output()
    up.note("ON CONFLICT ... WHERE content_hash IS DISTINCT FROM EXCLUDED.content_hash: unchanged rows return no row (skipped). P03 variant: the upsert is the guard.")
    agg = aggregate(wf, "Collect results")
    summary = code(wf, "Sync summary", SUMMARY_JS)
    adv = postgres_upsert(wf, "Advance watermark", "sync_state", ["source"], {
        "source": "={{ $json.source }}",
        "cursor": "={{ $json.cursor }}",
        "last_run_at": "={{ $json.last_run_at }}",
        "rows_seen": "={{ $json.fetched }}",
    }).retry(3, 1000)
    adv.note("Moves only after every changed row was written; re-run after a crash re-reads the same window (idempotent).")

    wf.chain(trg, cfg, wm, req, fetch, ok)
    wf.connect(manual, cfg)
    wf.connect(ok, has, out=0)
    wf.connect(ok, fail, out=1)
    wf.connect(has, split, out=0)
    wf.connect(has, none, out=1)
    wf.chain(split, canon, hsh, up, agg, summary, adv)
    wf.connect(none, agg)
    fail.at(1560, 220)      # keep the happy path on the top lane
    none.at(1820, 220)
    wf.sticky(
        "## D04 - Incremental sync (hash-based change detection)\n"
        "Watermark lives in `sync_state` (`mock-api-products`, cursor = max `updated_at` seen). Each run:\n\n"
        "1. `GET /products?updated_at_gte=<cursor>` through **P02** (backoff)\n"
        "2. canonical JSON of `{sku, name, price, stock}` -> SHA-256 `content_hash`\n"
        "3. `INSERT ... ON CONFLICT (id) DO UPDATE ... WHERE content_hash IS DISTINCT FROM EXCLUDED.content_hash` "
        "into `products_mirror` - unchanged rows write nothing (**P03**: the upsert *is* the idempotency guard)\n"
        "4. summary `{fetched, inserted, updated, skipped}` -> watermark advances **after** the writes\n\n"
        "Second run = no-op (0 inserted, 0 updated). Reset: `test/reset-watermark.sh`.",
        pos=(-40, -360), width=700, height=270)
    return wf


if __name__ == "__main__":
    build().save()
