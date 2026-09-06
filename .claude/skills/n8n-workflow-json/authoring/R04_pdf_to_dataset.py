#!/usr/bin/env python
"""R04 - PDF to Structured Fields to Dataset ("no API access" case).

Config (file) -> Read PDF -> docgen /pdf/extract (text + tables) -> Parse fields (Code: header fields by regex,
line items from the detected table, arithmetic check) -> documents row (meta = full record)
-> line items CSV + JSON written to data/out/ -> summary.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, catalog_id, code, cond_bool, convert, http, if_, manual_trigger,  # noqa: E402
                         postgres_insert, read_file, set_fields, split_out, stop_error, write_file)

PARSE_JS = r"""
// docgen /pdf/extract -> {filename, pages, text, tables:[{page, header, rows}], page_data}
const src = $input.first().json;
const text = String(src.text || '');
const grab = (re) => { const m = text.match(re); return m ? m[1].trim() : null; };
const num = (s) => { const n = Number(String(s ?? '').replace(/[^0-9.\-]/g, '')); return Number.isFinite(n) ? n : null; };

const header = {
  invoice_number: grab(/Invoice number:\s*([A-Z0-9-]+)/i),
  issue_date: grab(/Issue date:\s*(\d{4}-\d{2}-\d{2})/i),
  due_date: grab(/Due date:\s*(\d{4}-\d{2}-\d{2})/i),
  currency: grab(/\b(USD|EUR|GBP|LYD|EGP)\b/) || 'USD',
  subtotal: num(grab(/Subtotal\s+([0-9.,]+)/i)),
  vat: num(grab(/(?:VAT|Tax)[^\n]*?\s([0-9.,]+)\s*$/im)),
  total: num(grab(/^Total\b(?:\s*\([A-Z]{3}\))?(?: due)?[^\n0-9]*([0-9.,]+)\s*$/im)),   // ^ anchor so "Subtotal" never matches
  emails: Array.from(new Set(text.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}/g) || [])),
};

// Line items: the first table whose header has a description + amount column.
const isItems = (t) => (t.header || []).some(h => /desc/i.test(h)) && (t.header || []).some(h => /amount|total/i.test(h));
const table = (src.tables || []).find(isItems) || null;
const items = [];
if (table) {
  const cols = table.header.map(h => h.toLowerCase());
  const idx = (re) => cols.findIndex(c => re.test(c));
  const iDesc = idx(/desc/), iQty = idx(/qty|quantity/), iUnit = idx(/unit|price/), iAmt = idx(/amount|total/);
  for (const r of table.rows) {
    const amount = num(r[iAmt]);
    if (amount === null) continue;                          // skips subtotal/total rows if they were captured
    items.push({ description: r[iDesc], qty: num(r[iQty]), unit_price: num(r[iUnit]), amount,
                 line_ok: r[iQty] !== undefined && r[iUnit] !== undefined ? Math.abs(num(r[iQty]) * num(r[iUnit]) - amount) < 0.01 : null });
  }
}
const itemsSum = Math.round(items.reduce((a, i) => a + i.amount, 0) * 100) / 100;
const checks = {
  has_invoice_number: !!header.invoice_number,
  has_items: items.length > 0,
  items_sum_matches_subtotal: header.subtotal !== null ? Math.abs(itemsSum - header.subtotal) < 0.01 : null,
  every_line_ok: items.every(i => i.line_ok !== false),
};
const ok = Object.values(checks).every(v => v !== false);
return [{ json: { source_file: src.filename, pages: src.pages, ok, checks, header, items_sum: itemsSum,
                  items, item_count: items.length } }];
"""


def build() -> Workflow:
    wf = Workflow("R04", "pdf-to-dataset", "PDF to Structured Fields to Dataset", tags=["Documents"],
                  error_workflow=catalog_id("P01"),
                  description="Extracts header fields and the line-item table from a PDF invoice into a dataset.")
    trg = manual_trigger(wf, "Run once (manual / CLI)")
    cfg = set_fields(wf, "Config", {"file": "/home/node/.n8n-files/seed/invoice-locked.pdf"})
    rd = read_file(wf, "Read PDF", "={{ $json.file }}")
    ext = http(wf, "Extract text + tables (docgen)", "http://docgen:8090/pdf/extract", method="POST",
               form_binary=("file", "data"), timeout_ms=60000).retry(3, 2000)
    parse = code(wf, "Parse fields", PARSE_JS)
    valid = if_(wf, "Checks pass?", [cond_bool("={{ $json.ok }}")])
    bad = stop_error(wf, "Extraction failed checks", "=R04: {{ JSON.stringify($json.checks) }}")
    doc = postgres_insert(wf, "Record document", "documents", {
        "kind": "invoice-extract",
        "file_name": "={{ $json.source_file }}",
        "storage_key": "={{ $('Config').item.json.file }}",
        "meta": "={{ JSON.stringify({ header: $json.header, items: $json.items, checks: $json.checks, items_sum: $json.items_sum }) }}",
    }, returning=True)
    split = split_out(wf, "Split line items", "items")
    tag = set_fields(wf, "Tag rows", {
        "invoice_number": "={{ $('Parse fields').item.json.header.invoice_number }}",
        "issue_date": "={{ $('Parse fields').item.json.header.issue_date }}",
        "currency": "={{ $('Parse fields').item.json.header.currency }}",
    }, include_other=True)
    csv = convert(wf, "Line items to CSV", "csv",
                  file_name="=extract-{{ $('Parse fields').item.json.header.invoice_number }}.csv")
    save_csv = write_file(wf, "Write CSV", "=/home/node/.n8n-files/data/out/{{ $binary.data.fileName }}")
    to_json = code(wf, "Record to JSON file", """
// Build the JSON file in a Code node (base64 binary): Convert to File emitted nothing for a single DB row.
const rec = $('Parse fields').first().json;
const name = `extract-${rec.header.invoice_number}.json`;
const body = JSON.stringify({ document_id: $json.id, ...rec }, null, 2);
return [{ json: { fileName: name, bytes: body.length },
          binary: { data: { data: Buffer.from(body, 'utf8').toString('base64'), mimeType: 'application/json', fileName: name } } }];
""")
    save_json = write_file(wf, "Write JSON", "=/home/node/.n8n-files/data/out/{{ $binary.data.fileName }}")
    wf.chain(trg, cfg, rd, ext, parse, valid)
    wf.connect(valid, doc, out=0)
    wf.connect(valid, bad, out=1)
    wf.chain(doc, to_json, save_json)
    wf.connect(parse, split)
    wf.chain(split, tag, csv, save_csv)
    wf.sticky(
        "## R04 - PDF -> fields -> dataset\n"
        "The \"no API\" case: the only export is a PDF. **docgen** `/pdf/extract` (pdfplumber) returns the text and "
        "every detected table; the Code node pulls header fields with regexes, the line items from the table, and "
        "checks that the items add up to the printed subtotal before anything is stored.\n\n"
        "Outputs: `documents` row (meta = structured record), `data/out/extract-<invoice>.csv` (line items) and "
        "`.json`. Chain with R03 to turn the rows into an Arabic report.",
        pos=(-40, -340), width=640, height=240)
    return wf


if __name__ == "__main__":
    build().save()
