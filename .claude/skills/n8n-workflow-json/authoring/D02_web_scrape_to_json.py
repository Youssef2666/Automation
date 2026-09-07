#!/usr/bin/env python
"""D02 - Web Scrape to Structured JSON (HTML extraction + pagination).

Schedule / manual -> Config -> page loop:
  Next page -> P04 gate (shared key `d03-catalog`) -> [wait -> gate] -> Fetch page (HTTP, text, 3 attempts) ->
  Extract cards (one inner-HTML string + one data-sku per <article class="product">, the rel=next link) ->
  Any cards? -> Split Out (cards zipped with sku: one item per card) -> Extract card fields (second HTML pass,
  per card - a missing field cannot shift the other cards' values) -> Parse and validate page (sku/name/price/currency/stock rules + duplicate skus across
  pages) -> More pages? (follows rel=next, capped by max_pages) -> back to Next page.
Then: Rows (one item per card, _valid/_errors) -> Row valid? -> valid: JSON array file in data/out + MinIO
artifacts/scrapes/ + `documents` row (kind scrape, meta = counts); invalid: rejects JSON file in data/out.

`build("fixture")` emits test/fixture-variant.json: same nodes, but the page fetch is replaced by an inline
two-page fixture with deliberately broken cards, so validation and pagination can be exercised offline.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, catalog_id, code, cond_bool, cond_num, convert, execute_workflow,  # noqa: E402
                         html_extract, http, if_, manual_trigger, postgres_insert, s3_upload, schedule, set_fields,
                         split_out, wait, write_file)

NEXT_PAGE_JS = r"""
// First turn: the input is the Config item (no page yet). Later turns: the parsed page carrying the rows so far.
const prev = $input.first().json;
const cfg = $('Config').first().json;
const page = prev.page ? Number(prev.page) + 1 : 1;
return [{ json: { page, url: cfg.catalog_url, rows_so_far: prev.rows || [],
                  key: cfg.gate_key, limit: cfg.gate_limit, window_seconds: cfg.gate_window_seconds } }];
"""

PARSE_JS = r"""
// One input item per product card (fields extracted by the second HTML pass). Validate every card on its own,
// keep the bad ones (with _errors), and decide whether another page follows.
const state = $('Next page').last().json;
const cfg = $('Config').first().json;
const meta = $('Extract cards').first().json;
const page = Number(meta.page || state.page);
const scraped_at = new Date().toISOString();
const source_url = `${state.url}?page=${page}`;
const SKU = /^SKU-\d{4}$/;
const CURRENCY = /^[A-Z]{3}$/;
const text = (v) => (v === undefined || v === null) ? '' : String(v).replace(/\s+/g, ' ').trim();
const seen = new Set((state.rows_so_far || []).map(r => r.sku));
const cardsIn = $('One item per card').all();   // same order as $input (paired items): carries data-sku
const rows = $input.all().map((it, i) => {
  const c = it.json;
  const errors = [];
  const sku = text((cardsIn[i] || { json: {} }).json.sku).toUpperCase();
  const name = text(c.name);
  const category = text(c.category);
  const currency = text(c.currency).toUpperCase();
  const priceText = text(c.price);
  const price = priceText === '' ? NaN : Number(priceText.replace(/[^0-9.\-]/g, ''));
  const stockText = text(c.stock).toLowerCase();
  const stockMatch = stockText.match(/(\d+)\s+in stock/);
  const stock = stockMatch ? parseInt(stockMatch[1], 10) : (stockText === 'out of stock' ? 0 : null);
  if (!SKU.test(sku)) errors.push(`sku invalid: "${sku}"`);
  if (!name) errors.push('name missing');
  if (!category) errors.push('category missing');
  if (!Number.isFinite(price) || price <= 0) errors.push(`price invalid: "${priceText}"`);
  if (!CURRENCY.test(currency)) errors.push(`currency invalid: "${currency}"`);
  if (stock === null) errors.push(`stock unreadable: "${stockText}"`);
  if (SKU.test(sku)) {
    if (seen.has(sku)) errors.push('duplicate sku (already scraped on an earlier card or page)');
    else seen.add(sku);
  }
  return { sku, name, category, price: Number.isFinite(price) ? Math.round(price * 100) / 100 : null,
           currency, stock, in_stock: stock !== null && stock > 0,
           page, card: i + 1, source_url, scraped_at,
           _valid: errors.length === 0, _errors: errors.join('; ') };
});
const next = text(meta.next);
const pages = Number(meta.pages || 0);
const has_next = rows.length > 0 && page < Number(cfg.max_pages || 20)
              && (next !== '' || (pages > 0 && page < pages));
return [{ json: { page, pages, next, has_next, fetched: rows.length, rows: [...(state.rows_so_far || []), ...rows] } }];
"""

EMPTY_PAGE_JS = r"""
// The page had no product cards: stop paginating and hand the rows collected so far to the same output shape.
const state = $('Next page').last().json;
const meta = $('Extract cards').first().json;
return [{ json: { page: Number(meta.page || state.page), pages: Number(meta.pages || 0), next: '',
                  has_next: false, fetched: 0, rows: state.rows_so_far || [] } }];
"""

ROWS_JS = r"""
// All pages collected: one item per product card, valid or not.
const rows = $input.first().json.rows || [];
// Zero cards is a failure, not an empty dataset: the markup changed or the site is down. Fail loudly so P01 records it.
if (!rows.length) throw new Error('D02: no product cards matched the selectors (article.product) - markup changed?');
return rows.map(r => ({ json: r }));
"""

CLEAN_JS = r"""
// Drop the validation bookkeeping; what is left is the published row schema.
const { _valid, _errors, ...row } = $input.item.json;
return { json: row };
"""

SUMMARY_JS = r"""
// Runs once after the upload. Counts come from the Rows node (valid and rejected); the reject branch runs after
// this node (v1 order executes branches depth-first), so it is not referenced here.
const cfg = $('Config').first().json;
const all = $('Rows').all().map(i => i.json);
const rejected = all.filter(r => !r._valid);
const last = $('More pages?').last().json;
return [{ json: {
  source: cfg.catalog_url,
  pages: Number(last.page || 0),
  cards: all.length,
  valid: all.length - rejected.length,
  rejected: rejected.length,
  reject_errors: rejected.map(r => `page ${r.page} card ${r.card} (${r.sku || 'no sku'}): ${r._errors}`),
  file_name: cfg.file_name,
  storage_key: `s3://${cfg.bucket}/${cfg.s3_key}`,
  local_file: cfg.out_file,
  rejects_file: rejected.length ? cfg.rejects_file : null,
  run_at: new Date().toISOString(),
} }];
"""

# Two pages, five cards: one clean card, one with a missing price, one with a bad sku + unreadable stock, one
# duplicate of page 1's clean card on page 2, and one clean card on page 2. Page 2 has no rel=next link.
FIXTURE_JS = r"""
const page = Number($json.page || 1);
const card = (sku, name, cat, price, cur, stock) => `
      <article class="product" data-sku="${sku}">
        <h3 class="name">${name}</h3>
        <span class="category">${cat}</span>
        ${price === null ? '' : `<span class="price" data-currency="${cur}">${price}</span>`}
        <span class="stock">${stock}</span>
      </article>`;
const pages = {
  1: [card('SKU-9001', 'Fixture Desk Lamp', 'Office', '39.90', 'USD', '12 in stock'),
      card('SKU-9002', 'Fixture Notebook (no price)', 'Stationery', null, 'USD', '5 in stock'),
      card('SKU-BAD', 'Fixture Kettle', 'Kitchen', '24.00', 'USD', 'ships soon')],
  2: [card('SKU-9001', 'Fixture Desk Lamp (duplicate)', 'Office', '39.90', 'USD', '12 in stock'),
      card('SKU-9003', 'Fixture Camp Stove', 'Outdoor', '58.50', 'USD', 'out of stock')],
};
const cards = pages[page] || [];
const next = page < 2 ? `<a rel="next" href="/catalog?page=${page + 1}">Next</a>` : '';
const html = `<!doctype html><html><body><main id="catalog" data-page="${page}" data-pages="2">
  <h1>Fixture catalog</h1>${cards.join('\n')}
  <nav class="pagination"><span class="page">Page ${page} of 2</span>${next}</nav>
</main></body></html>`;
return [{ json: { ok: true, status: 200, attempts: 1, data: html, url: `fixture://catalog?page=${page}` } }];
"""


def build(variant: str = "main") -> Workflow:
    fixture = variant == "fixture"
    if fixture:
        wf = Workflow("D02", "fixture-variant", "Web Scrape (fixture test variant)", tags=["test"],
                      error_workflow=catalog_id("P01"))
    else:
        wf = Workflow("D02", "web-scrape-to-json", "Web Scrape to Structured JSON", tags=["Data & ETL"],
                      error_workflow=catalog_id("P01"),
                      description="Scrapes a paginated HTML catalog page by page, validates every product card, "
                                  "and publishes the rows as a JSON dataset in MinIO + a documents record.")
    stem = "catalog-scrape-fixture" if fixture else "catalog-scrape"

    trg = schedule(wf, "Daily at 05:30", daily_at=(5, 30))
    manual = manual_trigger(wf, "Run once (manual / CLI)")
    cfg = set_fields(wf, "Config", {
        "catalog_url": "http://mock-api:8080/catalog",
        "max_pages": 20,
        "gate_key": "d03-catalog",
        "gate_limit": 5,
        "gate_window_seconds": 10,
        "bucket": "artifacts",
        "file_name": f"={stem}-{{{{ $now.toFormat('yyyyLLdd') }}}}.json",
        "s3_key": f"=scrapes/{stem}-{{{{ $now.toFormat('yyyyLLdd') }}}}.json",
        "out_file": f"/home/node/.n8n-files/data/out/{stem}.json",
        "rejects_file": f"/home/node/.n8n-files/data/out/{stem}-rejects.json",
    })
    cfg.note("Point `catalog_url` at any site whose product cards match the selectors in `Extract cards` / "
             "`Extract card fields`; `max_pages` caps a runaway rel=next chain.")

    # --- page loop ------------------------------------------------------------------------------------------
    nxt = code(wf, "Next page", NEXT_PAGE_JS)
    if fixture:
        fetch = code(wf, "Fixture page (no network)", FIXTURE_JS)
    else:
        gate = execute_workflow(wf, "Rate limit gate (P04)", catalog_id("P04"), cached_name="P04 - Rate limit gate")
        allowed = if_(wf, "Allowed?", [cond_bool("={{ $json.allowed }}")])
        hold = wait(wf, "Wait for next window", amount="={{ ($json.retry_after_ms + 250) / 1000 }}", unit="seconds")
        # Direct HTTP node, not P02: P02's HTTP node autodetects text/html as a file body and its Result node only
        # returns JSON (`data: null` for HTML). Node-level retries cover 5xx/network; the gate covers 429.
        fetch = http(wf, "Fetch page (HTML)", "={{ $('Next page').last().json.url }}",
                     query={"page": "={{ $('Next page').last().json.page }}"},
                     response="text", timeout_ms=10000).retry(3, 1000)
    cards = html_extract(wf, "Extract cards", [
        {"key": "cards", "cssSelector": "article.product", "returnValue": "html", "returnArray": True},
        # `html` is the card's inner HTML, so the attribute on <article> itself is read here, once per element.
        {"key": "sku", "cssSelector": "article.product", "returnValue": "attribute", "attribute": "data-sku", "returnArray": True},
        {"key": "next", "cssSelector": "nav.pagination a[rel=next]", "returnValue": "attribute", "attribute": "href",
         "returnArray": False},
        {"key": "page", "cssSelector": "#catalog", "returnValue": "attribute", "attribute": "data-page", "returnArray": False},
        {"key": "pages", "cssSelector": "#catalog", "returnValue": "attribute", "attribute": "data-pages", "returnArray": False},
    ], source="json", prop="data")
    any_cards = if_(wf, "Any cards?", [cond_num("={{ ($json.cards || []).length }}", "gt", 0)])
    per_card = split_out(wf, "One item per card", "cards, sku")   # zipped: cards[i] with sku[i]
    fields = html_extract(wf, "Extract card fields", [
        {"key": "name", "cssSelector": ".name", "returnValue": "text", "returnArray": False},
        {"key": "category", "cssSelector": ".category", "returnValue": "text", "returnArray": False},
        {"key": "price", "cssSelector": ".price", "returnValue": "text", "returnArray": False},
        {"key": "currency", "cssSelector": ".price", "returnValue": "attribute", "attribute": "data-currency", "returnArray": False},
        {"key": "stock", "cssSelector": ".stock", "returnValue": "text", "returnArray": False},
    ], source="json", prop="cards")
    parse = code(wf, "Parse and validate page", PARSE_JS)
    empty = code(wf, "Empty page", EMPTY_PAGE_JS)
    more = if_(wf, "More pages?", [cond_bool("={{ $json.has_next }}")])

    # --- rows out -------------------------------------------------------------------------------------------
    rows = code(wf, "Rows", ROWS_JS)
    valid = if_(wf, "Row valid?", [cond_bool("={{ $json._valid }}")])
    clean = code(wf, "Clean rows", CLEAN_JS, per_item=True)
    to_json = convert(wf, "Rows to JSON file", "toJson", file_name="={{ $('Config').first().json.file_name }}",
                      format=True)
    wr = write_file(wf, "Write data/out JSON", "={{ $('Config').first().json.out_file }}")
    up = s3_upload(wf, "Upload to MinIO (artifacts)", "artifacts", "={{ $('Config').first().json.s3_key }}").retry(3, 1000)
    summary = code(wf, "Summarize run", SUMMARY_JS)
    doc = postgres_insert(wf, "Record dataset (documents)", "documents", {
        "kind": "scrape",
        "file_name": "={{ $json.file_name }}",
        "storage_key": "={{ $json.storage_key }}",
        "meta": "={{ JSON.stringify($json) }}",
    }, returning=True).retry(3, 1000)
    rej_json = convert(wf, "Rejects to JSON file", "toJson", file_name=f"{stem}-rejects.json", format=True)
    rej_wr = write_file(wf, "Write rejects file", "={{ $('Config').first().json.rejects_file }}")

    wf.chain(trg, cfg)
    wf.connect(manual, cfg)
    wf.chain(cfg, nxt)
    if fixture:
        wf.chain(nxt, fetch, cards)
    else:
        wf.chain(nxt, gate, allowed)
        wf.connect(allowed, fetch, out=0)
        wf.connect(allowed, hold, out=1)
        wf.connect(hold, gate)
        wf.chain(fetch, cards)
        hold.at(780, 660)
    wf.chain(cards, any_cards)
    wf.connect(any_cards, per_card, out=0)
    wf.connect(any_cards, empty, out=1)
    wf.chain(per_card, fields, parse, more)
    wf.connect(empty, more)
    wf.connect(more, nxt, out=0)          # yes: next page (cycle back)
    wf.connect(more, rows, out=1)         # no: publish
    wf.chain(rows, valid)
    wf.connect(valid, clean, out=0)
    wf.connect(valid, rej_json, out=1)
    wf.chain(clean, to_json, wr, up, summary, doc)
    wf.chain(rej_json, rej_wr)
    wf.sticky(
        "## D02 - Scrape, paginate, validate, publish\n"
        "`/catalog?page=N` (14 cards per page, `rel=next` link) fetched one page per turn: **P04** gate "
        "(`d03-catalog`, shared with D03 - one budget per site) -> HTTP Request (text, 3 attempts 1 s apart).\n\n"
        "Two HTML passes: page -> one inner-HTML string + `data-sku` per `article.product`, then one item per "
        "card -> fields. "
        "A card missing a field can never shift its neighbours' values (parallel arrays would).\n\n"
        "`Parse and validate page` checks sku / name / category / price / currency / stock and duplicate SKUs "
        "across pages; `More pages?` follows `rel=next` (cap `max_pages`). Valid rows -> JSON array file in "
        "`data/out/` + MinIO `artifacts/scrapes/` + `documents` row (kind `scrape`, meta = counts); rejected "
        "cards -> `data/out/catalog-scrape-rejects.json`. **P01** handles failures.",
        pos=(-40, -420), width=720, height=330)
    return wf


if __name__ == "__main__":
    main = build()
    main.save()
    build("fixture").save(main.folder() / "test" / "fixture-variant.json")
