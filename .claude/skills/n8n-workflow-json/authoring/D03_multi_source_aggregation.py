#!/usr/bin/env python
"""D03 - Multi-source API Aggregation -> normalized dataset.

Three sources, three shapes, one schema:
  1. mock-api /products      json-server collection (_page/_limit), fetched through P02 (backoff)
  2. mock-api /catalog?page= paginated HTML product cards, one page per loop turn, each page gated by P04
  3. Postgres products       table with the same columns but DB types

Every source is normalized in a Code node to {sku, name, category, price_usd, stock, source, seen_at, updated_at},
appended (Merge), sorted by updated_at desc and de-duplicated on sku (freshest wins), then written to
data/out/catalog-normalized.csv, uploaded to MinIO artifacts/datasets/ and recorded in `documents` (kind dataset).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, catalog_id, code, cond_bool, convert, dedupe, execute_workflow,  # noqa: E402
                         html_extract, http, if_, manual_trigger, merge, postgres_insert, postgres_select, s3_upload,
                         schedule, set_fields, sort, stop_error, wait, write_file)

# Shared by the three normalizers: one target schema, one place to fix a rule.
NORMALIZE_FN = r"""
const RATES_TO_USD = { USD: 1, EUR: 1.08, GBP: 1.27, LYD: 0.21, EGP: 0.021, TRY: 0.029, AED: 0.27, SAR: 0.27 };
function normalize(r) {
  const price = Number(String(r.price ?? '').replace(/[^0-9.\-]/g, ''));
  const rate = RATES_TO_USD[String(r.currency || 'USD').toUpperCase()] ?? 1;
  const stockMatch = String(r.stock ?? '').match(/-?\d+/);
  const ts = r.updated_at ? new Date(r.updated_at) : null;
  return {
    sku: String(r.sku || '').trim().toUpperCase(),
    name: String(r.name || '').trim(),
    category: String(r.category || '').trim(),
    price_usd: Number.isFinite(price) ? Math.round(price * rate * 100) / 100 : null,
    stock: stockMatch ? Math.max(0, parseInt(stockMatch[0], 10)) : 0,
    source: r.source,
    seen_at: r.seen_at,
    updated_at: ts && !isNaN(ts.getTime()) ? ts.toISOString() : '',   // '' sorts last -> dated rows win ties
  };
}
"""

NORM_PRODUCTS_JS = NORMALIZE_FN + r"""
// Source 1 - json-server: P02 returns {ok, status, data: [{id, sku, name, category, price, currency, stock, updated_at}]}.
const seen_at = new Date().toISOString();
const rows = Array.isArray($json.data) ? $json.data : [];
return rows.map(p => ({ json: normalize({ ...p, source: 'mock-api-products', seen_at }) }));
"""

NORM_DB_JS = NORMALIZE_FN + r"""
// Source 3 - Postgres: numeric comes back as a string, timestamptz as an ISO string.
const seen_at = new Date().toISOString();
return $input.all().map(i => ({ json: normalize({ ...i.json, source: 'postgres-products', seen_at }) }));
"""

NEXT_PAGE_JS = r"""
// First turn: the input is the Config item (no page yet). Later turns: the parsed page carrying the rows so far.
const prev = $input.first().json;
const cfg = $('Config').first().json;
const page = prev.page ? Number(prev.page) + 1 : 1;
return [{ json: { page, url: `${cfg.catalog_url}?page=${page}`, rows_so_far: prev.rows || [],
                  key: cfg.gate_key, limit: cfg.gate_limit, window_seconds: cfg.gate_window_seconds } }];
"""

PARSE_CATALOG_JS = r"""
// Source 2 - HTML: the HTML node returns parallel arrays (one entry per product card) plus the page counters.
const x = $input.first().json;
const state = $('Next catalog page').last().json;
const arr = (v) => Array.isArray(v) ? v : (v === undefined || v === null || v === '' ? [] : [v]);
const skus = arr(x.sku), names = arr(x.name), cats = arr(x.category), prices = arr(x.price),
      curs = arr(x.currency), stocks = arr(x.stock);
const seen_at = new Date().toISOString();
const rows = skus.map((sku, i) => ({ sku, name: names[i], category: cats[i], price: prices[i], currency: curs[i],
                                     stock: stocks[i], updated_at: null, source: 'mock-api-catalog', seen_at }));
const page = Number(x.page || state.page), pages = Number(x.pages || 1);
return [{ json: { page, pages, has_next: page < pages && rows.length > 0, fetched: rows.length,
                  rows: [...state.rows_so_far, ...rows] } }];
"""

CATALOG_ROWS_JS = NORMALIZE_FN + r"""
// All pages collected: one item per product card.
return ($input.first().json.rows || []).map(r => ({ json: normalize(r) }));
"""

SUMMARY_JS = r"""
// Runs once after the upload. Counts come from the nodes before the CSV; the file names from Config / today.
const cfg = $('Config').first().json;
const all = $('Merge sources').all().map(i => i.json);
const kept = $('Keep freshest per sku').all().map(i => i.json);
const count = (rows) => rows.reduce((a, r) => { a[r.source] = (a[r.source] || 0) + 1; return a; }, {});
const date = $now.toFormat('yyyyLLdd');
return [{ json: {
  total: kept.length,
  fetched: all.length,
  per_source: count(all),
  kept_per_source: count(kept),
  duplicates_removed: all.length - kept.length,
  catalog_pages: Number($('Parse catalog page').last().json.page || 0),
  file_name: `catalog-normalized-${date}.csv`,
  storage_key: `s3://${cfg.bucket}/datasets/catalog-normalized-${date}.csv`,
  local_file: cfg.out_file,
  run_at: new Date().toISOString(),
} }];
"""


def build() -> Workflow:
    wf = Workflow("D03", "multi-source-aggregation", "Multi-source API Aggregation", tags=["Data & ETL"],
                  error_workflow=catalog_id("P01"),
                  description="Three product feeds (JSON API, paginated HTML, Postgres) normalized into one "
                              "de-duplicated dataset in MinIO + documents.")
    trg = schedule(wf, "Daily at 06:00", daily_at=(6, 0))
    manual = manual_trigger(wf, "Run once (manual / CLI)")   # `n8n execute` needs a manual trigger
    cfg = set_fields(wf, "Config", {
        "products_url": "http://mock-api:8080/products",
        "catalog_url": "http://mock-api:8080/catalog",
        "gate_key": "d03-catalog",
        "gate_limit": 5,
        "gate_window_seconds": 10,
        "bucket": "artifacts",
        "out_file": "/home/node/.n8n-files/data/out/catalog-normalized.csv",
    })

    # --- source 1: JSON collection through P02 -------------------------------------------------------------
    p_req = set_fields(wf, "Products request", {
        "url": "={{ $json.products_url }}",
        "query": ("={{ { _page: 1, _limit: 100, _sort: 'sku', _order: 'asc' } }}", "object"),
        "max_attempts": 5,
        "base_ms": 500,
    })
    p_fetch = execute_workflow(wf, "Fetch /products (P02)", catalog_id("P02"), cached_name="P02 - HTTP with backoff")
    p_ok = if_(wf, "Products fetched?", [cond_bool("={{ $json.ok }}")])
    p_fail = stop_error(wf, "Products source failed",
                        "=D03 /products failed after {{ $json.attempts }} attempt(s): {{ $json.response }}")
    p_norm = code(wf, "Normalize products (JSON)", NORM_PRODUCTS_JS)

    # --- source 2: paginated HTML, one page per turn, each page asks the P04 gate first -------------------
    c_next = code(wf, "Next catalog page", NEXT_PAGE_JS)
    c_gate = execute_workflow(wf, "Rate limit gate (P04)", catalog_id("P04"), cached_name="P04 - Rate limit gate")
    c_allowed = if_(wf, "Allowed?", [cond_bool("={{ $json.allowed }}")])
    c_hold = wait(wf, "Wait for next window", amount="={{ ($json.retry_after_ms + 250) / 1000 }}", unit="seconds")
    c_fetch = http(wf, "Fetch catalog page (HTML)", "={{ $('Next catalog page').last().json.url }}",
                   response="text", timeout_ms=10000).retry(3, 1000)
    c_extract = html_extract(wf, "Extract product cards", [
        {"key": "sku", "cssSelector": "article.product", "returnValue": "attribute", "attribute": "data-sku", "returnArray": True},
        {"key": "name", "cssSelector": "article.product .name", "returnValue": "text", "returnArray": True},
        {"key": "category", "cssSelector": "article.product .category", "returnValue": "text", "returnArray": True},
        {"key": "price", "cssSelector": "article.product .price", "returnValue": "text", "returnArray": True},
        {"key": "currency", "cssSelector": "article.product .price", "returnValue": "attribute", "attribute": "data-currency", "returnArray": True},
        {"key": "stock", "cssSelector": "article.product .stock", "returnValue": "text", "returnArray": True},
        {"key": "page", "cssSelector": "#catalog", "returnValue": "attribute", "attribute": "data-page", "returnArray": False},
        {"key": "pages", "cssSelector": "#catalog", "returnValue": "attribute", "attribute": "data-pages", "returnArray": False},
    ], source="json", prop="data")
    c_parse = code(wf, "Parse catalog page", PARSE_CATALOG_JS)
    c_more = if_(wf, "More pages?", [cond_bool("={{ $json.has_next }}")])
    c_rows = code(wf, "Normalize catalog (HTML)", CATALOG_ROWS_JS)

    # --- source 3: Postgres table ------------------------------------------------------------------------
    db = postgres_select(wf, "Read products table", "products", sort=("sku", "asc")).retry(3, 1000)
    db_norm = code(wf, "Normalize products (Postgres)", NORM_DB_JS)

    # --- merge, freshest wins, dataset out ---------------------------------------------------------------
    mg = merge(wf, "Merge sources", "append", inputs=3)
    srt = sort(wf, "Sort by updated_at desc", [("updated_at", "descending")])
    dd = dedupe(wf, "Keep freshest per sku", ["sku"])
    csv = convert(wf, "Rows to CSV", "csv", file_name="catalog-normalized.csv")
    wr = write_file(wf, "Write data/out CSV", "={{ $('Config').first().json.out_file }}")
    up = s3_upload(wf, "Upload to MinIO (artifacts)", "artifacts",
                   "=datasets/catalog-normalized-{{ $now.toFormat('yyyyLLdd') }}.csv").retry(3, 1000)
    summary = code(wf, "Summarize run", SUMMARY_JS)
    doc = postgres_insert(wf, "Record dataset (documents)", "documents", {
        "kind": "dataset",
        "file_name": "={{ $json.file_name }}",
        "storage_key": "={{ $json.storage_key }}",
        "meta": "={{ JSON.stringify($json) }}",
    }, returning=True)

    wf.chain(trg, cfg)
    wf.connect(manual, cfg)
    # source 1
    wf.chain(cfg, p_req, p_fetch, p_ok)
    wf.connect(p_ok, p_norm, out=0)
    wf.connect(p_ok, p_fail, out=1)
    # source 2 (cycle: Next page -> gate -> [wait -> gate] -> fetch -> extract -> parse -> more? -> Next page)
    wf.connect(cfg, c_next)
    wf.chain(c_next, c_gate, c_allowed)
    wf.connect(c_allowed, c_fetch, out=0)
    wf.connect(c_allowed, c_hold, out=1)
    wf.connect(c_hold, c_gate)
    wf.chain(c_fetch, c_extract, c_parse, c_more)
    wf.connect(c_more, c_next, out=0)
    wf.connect(c_more, c_rows, out=1)
    # source 3
    wf.connect(cfg, db)
    wf.connect(db, db_norm)
    # merge: Postgres first so it wins ties on updated_at, then the JSON API, then the HTML (no timestamp)
    wf.connect(db_norm, mg, inp=0)
    wf.connect(p_norm, mg, inp=1)
    wf.connect(c_rows, mg, inp=2)
    wf.chain(mg, srt, dd, csv, wr, up, summary, doc)
    c_hold.at(1300, 900)
    wf.sticky(
        "## D03 - Three shapes, one schema\n"
        "1. `/products` (json-server, `_page/_limit`) via **P02** backoff\n"
        "2. `/catalog?page=N` (HTML cards, `data-pages`) - one page per turn, each page asks the **P04** gate "
        "(`d03-catalog`, 5 per 10 s) and waits `retry_after_ms` when throttled\n"
        "3. Postgres `products` (numeric/timestamptz types)\n\n"
        "Each source -> `{sku, name, category, price_usd, stock, source, seen_at, updated_at}`; Merge -> sort by "
        "`updated_at` desc -> Remove Duplicates on `sku` (freshest wins, Postgres wins ties, HTML rows have no "
        "date and only win when nobody else has the SKU) -> CSV in `data/out/` + MinIO `artifacts/datasets/` + "
        "`documents` row (meta = counts).",
        pos=(-40, -380), width=700, height=290)
    return wf


if __name__ == "__main__":
    build().save()
