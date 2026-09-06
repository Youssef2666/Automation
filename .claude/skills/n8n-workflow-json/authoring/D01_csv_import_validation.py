#!/usr/bin/env python
"""D01 - CSV/XLSX Import with Row-level Validation.

Manual Trigger -> Config (file path + format) -> Read file -> Extract (csv | xlsx) -> Validate rows (every row gets
_valid/_errors/_row) -> valid rows: upsert customers on external_id; invalid rows: rejects CSV written to
data/out/ and e-mailed via Mailpit -> summary item {imported, rejected, reject_file}. Never all-or-nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, aggregate, catalog_id, code, cond_bool, cond_str, convert, email,  # noqa: E402
                         extract, if_, manual_trigger, postgres_upsert, read_file, set_fields, write_file)

VALIDATE_JS = r"""
// One item per spreadsheet row. Validate each row independently and keep every row (valid or not).
const rows = $input.all().map(i => i.json);
const seen = new Map();
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const SEGMENTS = ['smb', 'mid', 'enterprise'];
const clean = (v) => (v === undefined || v === null) ? '' : String(v).trim();
return rows.map((raw, idx) => {
  const r = {
    external_id: clean(raw.external_id), name: clean(raw.name), email: clean(raw.email).toLowerCase(),
    phone: clean(raw.phone), company: clean(raw.company), country: clean(raw.country), city: clean(raw.city),
    segment: clean(raw.segment).toLowerCase(),
  };
  const errors = [];
  if (!r.external_id) errors.push('external_id missing');
  if (!r.name) errors.push('name missing');
  if (!EMAIL.test(r.email)) errors.push(`email invalid: "${r.email}"`);
  if (r.segment && !SEGMENTS.includes(r.segment)) errors.push(`segment not in ${SEGMENTS.join('/')}: "${r.segment}"`);
  if (r.external_id) {
    if (seen.has(r.external_id)) errors.push(`duplicate external_id (first seen on row ${seen.get(r.external_id)})`);
    else seen.set(r.external_id, idx + 2);
  }
  return { json: { ...r, _row: idx + 2, _valid: errors.length === 0, _errors: errors.join('; ') } };
});
"""

SUMMARY_JS = r"""
// Runs once after the valid rows were upserted. Counts come from the validator; the reject branch runs
// after this node (n8n v1 order executes branches depth-first), so it is not referenced here.
const all = $('Validate rows').all().map(i => i.json);
const rejected = all.filter(r => !r._valid);
return [{ json: {
  file: $('Config').first().json.file,
  rows: all.length,
  imported: all.length - rejected.length,
  rejected: rejected.length,
  reject_rows: rejected.map(r => r._row),
  reject_errors: rejected.map(r => `row ${r._row}: ${r._errors}`),
  reject_file: rejected.length ? 'data/out/rejects-<timestamp>.csv (written on the reject branch)' : null,
}}];
"""


def build(fmt: str = "csv") -> Workflow:
    if fmt == "csv":
        wf = Workflow("D01", "csv-import-validation", "CSV/XLSX Import with Row-level Validation", tags=["Data & ETL"],
                      error_workflow=catalog_id("P01"),
                      description="Imports a customers file row by row: good rows are upserted, bad rows reported.")
    else:  # test fixture: same workflow, Config preset to the XLSX seed file, different id
        wf = Workflow("D01", "xlsx-variant", "CSV/XLSX Import (XLSX test variant)", tags=["test"],
                      error_workflow=catalog_id("P01"))
    trg = manual_trigger(wf, "Run once (manual / CLI)")
    cfg = set_fields(wf, "Config", {
        "file": f"/home/node/.n8n-files/seed/customers.{fmt}",
        "format": fmt,
        "report_to": "ops@lab.local",
    })
    cfg.note("Point `file` at /home/node/.n8n-files/data/inbox/<your file> and set format csv|xlsx.")
    rd = read_file(wf, "Read file", "={{ $json.file }}")
    fmt = if_(wf, "XLSX?", [cond_str("={{ $('Config').item.json.format }}", "equals", "xlsx")])
    ex_xlsx = extract(wf, "Extract XLSX", "xlsx", headerRow=True)
    ex_csv = extract(wf, "Extract CSV", "csv", headerRow=True)
    val = code(wf, "Validate rows", VALIDATE_JS)
    ok = if_(wf, "Row valid?", [cond_bool("={{ $json._valid }}")])
    up = postgres_upsert(wf, "Upsert customers", "customers", ["external_id"], {
        "external_id": "={{ $json.external_id }}",
        "name": "={{ $json.name }}",
        "email": "={{ $json.email }}",
        "phone": "={{ $json.phone }}",
        "company": "={{ $json.company }}",
        "country": "={{ $json.country }}",
        "city": "={{ $json.city }}",
        "segment": "={{ $json.segment || 'smb' }}",
    }).retry(3, 1000)
    agg = aggregate(wf, "Collect imported")
    summary = code(wf, "Summary", SUMMARY_JS)
    rej_csv = convert(wf, "Rejects to CSV", "csv", file_name="=rejects-{{ $now.toFormat('yyyyLLdd-HHmmss') }}.csv")
    rej_write = write_file(wf, "Write rejects file", "=/home/node/.n8n-files/data/out/{{ $binary.data.fileName }}")
    rej_mail = email(wf, "Email rejects (Mailpit)", "={{ $('Config').item.json.report_to }}",
                     "=[Automation Lab] D01 import: {{ $('Validate rows').all().filter(i => !i.json._valid).length }} row(s) rejected",
                     html="=<p>Import of <code>{{ $('Config').item.json.file }}</code>: "
                          "<b>{{ $('Validate rows').all().filter(i => i.json._valid).length }}</b> rows imported, "
                          "<b>{{ $('Validate rows').all().filter(i => !i.json._valid).length }}</b> rejected.</p>"
                          "<p>Rejected rows (attached as CSV):</p><ul>"
                          "{{ $('Validate rows').all().filter(i => !i.json._valid).map(i => '<li>row ' + i.json._row + ': ' + i.json._errors + '</li>').join('') }}"
                          "</ul>",
                     attachments="data").retry(3, 2000)
    wf.chain(trg, cfg, rd, fmt)
    wf.connect(fmt, ex_xlsx, out=0)
    wf.connect(fmt, ex_csv, out=1)
    wf.connect(ex_xlsx, val)
    wf.connect(ex_csv, val)
    wf.chain(val, ok)
    wf.connect(ok, up, out=0)
    wf.connect(ok, rej_csv, out=1)
    wf.chain(up, agg, summary)
    wf.chain(rej_csv, rej_write, rej_mail)
    wf.sticky(
        "## D01 - Row-level import\n"
        "Reads `customers.csv` (or `customers.xlsx`) from the seed mount, validates **every row on its own** "
        "(required fields, e-mail, segment, duplicates inside the file), upserts the good rows on `external_id` "
        "and turns the bad ones into `data/out/rejects-<ts>.csv` + an e-mail.\n\n"
        "A bad row never blocks the good ones, and re-running is idempotent (upsert).",
        pos=(-40, -330), width=620, height=210)
    return wf


if __name__ == "__main__":
    main = build()
    main.save()
    build("xlsx").save(main.folder() / "test" / "xlsx-variant.json")
