#!/usr/bin/env python
"""R02 - Bulk Certificate Generation from CSV.

Config (csv) -> Read + Extract CSV -> one HTML certificate per row (Code, per item) -> docgen /render/pdf per row
-> write each PDF to data/out/certificate-<name>.pdf -> bundle all PDFs into one item -> zip -> data/out/ + MinIO reports/
-> documents row + summary e-mail.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, catalog_id, code, compress, email, extract, http, manual_trigger,  # noqa: E402
                         postgres_insert, read_file, s3_upload, set_fields, write_file)

CERT_JS = r"""
// Per row: {full_name, email, course, completed_on, hours, instructor} -> HTML certificate (A4 landscape).
const r = $json;
const slug = String(r.full_name || 'attendee').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
const id = `CERT-${String(r.completed_on || '').replace(/-/g, '')}-${String($itemIndex + 1).padStart(3, '0')}`;
const html = `<!doctype html><html><head><meta charset="utf-8"><style>
@page { size: A4 landscape; margin: 0 }
html,body{margin:0;width:297mm;height:210mm;font-family:'DejaVu Sans',sans-serif;color:#1d2b3a}
/* explicit offsets: WeasyPrint 62 does not support the inset shorthand */
.frame{position:absolute;top:14mm;left:14mm;right:14mm;bottom:14mm;border:6px double #b08d2c;padding:14mm 18mm;text-align:center}
h1{font-size:34pt;letter-spacing:4px;margin:0 0 6mm;color:#b08d2c} .sub{font-size:13pt;color:#666}
.name{font-size:30pt;margin:10mm 0 4mm;border-bottom:2px solid #1d2b3a;display:inline-block;padding:0 12mm 2mm}
.course{font-size:18pt;margin:6mm 0} .meta{font-size:11pt;color:#555;margin-top:12mm}
.sig{display:flex;justify-content:space-around;margin-top:16mm;font-size:11pt}
.sig div{border-top:1px solid #1d2b3a;padding-top:2mm;width:60mm} .id{position:absolute;bottom:8mm;right:12mm;font-size:8pt;color:#999}
</style></head><body><div class="frame">
<h1>CERTIFICATE</h1><div class="sub">of completion</div>
<div class="sub" style="margin-top:8mm">This certifies that</div>
<div class="name">${r.full_name}</div>
<div class="sub">has successfully completed</div>
<div class="course">${r.course}</div>
<div class="meta">${r.hours} hours · completed on ${r.completed_on}</div>
<div class="sig"><div>${r.instructor}<br><small>Instructor</small></div><div>Automation Lab Academy<br><small>Programme director</small></div></div>
<div class="id">${id} · synthetic demo document (R02)</div>
</div></body></html>`;
return { json: { ...r, certificate_id: id, filename: `${slug}-${String(r.course || '').toLowerCase().replace(/[^a-z0-9]+/g, '-')}.pdf`, html } };
"""

BUNDLE_JS = r"""
// Collect every rendered PDF (binary "data" on each item) into ONE item with binary file0..fileN,
// so the Compression node can zip them together.
const items = $input.all();
const binary = {};
items.forEach((it, i) => { if (it.binary && it.binary.data) binary[`file${i}`] = it.binary.data; });
const names = items.map(it => it.binary?.data?.fileName || `certificate-${it.json.certificate_id}.pdf`);
return [{ json: { count: Object.keys(binary).length, files: names, keys: Object.keys(binary).join(','),
                  zip_name: `certificates-${$now.toFormat('yyyyLLdd-HHmmss')}.zip` }, binary }];
"""


def build() -> Workflow:
    wf = Workflow("R02", "bulk-certificates", "Bulk Certificate Generation from CSV", tags=["Documents"],
                  error_workflow=catalog_id("P01"),
                  description="One PDF certificate per CSV row via docgen, zipped, stored in MinIO, e-mailed.")
    trg = manual_trigger(wf, "Run once (manual / CLI)")
    cfg = set_fields(wf, "Config", {"file": "/home/node/.n8n-files/seed/certificates.csv", "report_to": "training@lab.local"})
    rd = read_file(wf, "Read CSV", "={{ $json.file }}")
    ex = extract(wf, "Extract rows", "csv", headerRow=True)
    tpl = code(wf, "Certificate HTML (per row)", CERT_JS, per_item=True)
    render = http(wf, "Render PDF (docgen)", "http://docgen:8090/render/pdf", method="POST",
                  json_body="={{ JSON.stringify({ html: $json.html, filename: $json.filename }) }}",
                  response="file", timeout_ms=60000, batching=(2, 200)).retry(3, 2000)
    save_each = write_file(wf, "Write each PDF", "=/home/node/.n8n-files/data/out/certificate-{{ $('Certificate HTML (per row)').item.json.filename }}")
    bundle = code(wf, "Bundle PDFs into one item", BUNDLE_JS)
    zipped = compress(wf, "Zip certificates", "={{ $json.zip_name }}", prop="={{ $json.keys }}")
    save_zip = write_file(wf, "Write ZIP", "=/home/node/.n8n-files/data/out/{{ $('Bundle PDFs into one item').item.json.zip_name }}")
    upload = s3_upload(wf, "Upload ZIP (MinIO)", "reports", "=certificates/{{ $('Bundle PDFs into one item').item.json.zip_name }}").retry(3, 1000)
    doc = postgres_insert(wf, "Record document", "documents", {
        "kind": "certificates-zip",
        "file_name": "={{ $('Bundle PDFs into one item').item.json.zip_name }}",
        "storage_key": "=s3://reports/certificates/{{ $('Bundle PDFs into one item').item.json.zip_name }}",
        "meta": "={{ JSON.stringify({ count: $('Bundle PDFs into one item').item.json.count, files: $('Bundle PDFs into one item').item.json.files }) }}",
    })
    mail = email(wf, "Email bundle (Mailpit)", "={{ $('Config').item.json.report_to }}",
                 "=[Automation Lab] {{ $('Bundle PDFs into one item').item.json.count }} certificates generated",
                 html="=<p>{{ $('Bundle PDFs into one item').item.json.count }} certificates rendered from "
                      "<code>{{ $('Config').item.json.file }}</code>; ZIP attached and stored in MinIO "
                      "(reports/certificates/).</p><ul>{{ $('Bundle PDFs into one item').item.json.files.map(f => '<li>' + f + '</li>').join('') }}</ul>",
                 attachments="data").retry(3, 2000)
    wf.chain(trg, cfg, rd, ex, tpl, render, save_each, bundle, zipped, save_zip)
    wf.chain(save_zip, upload, doc)
    wf.connect(save_zip, mail)
    wf.sticky(
        "## R02 - Bulk certificates from CSV\n"
        "Each CSV row becomes an HTML certificate (Code, per item) rendered by **docgen** `/render/pdf` "
        "(batched 2 at a time). PDFs are written individually, then bundled into one item and zipped "
        "(Compression node), stored in `data/out/` and MinIO `reports/certificates/`, logged in `documents` and "
        "e-mailed.\n\n"
        "Swap the CSV for a Postgres query or a form (T05) to drive it from live data.",
        pos=(-40, -330), width=640, height=220)
    return wf


if __name__ == "__main__":
    build().save()
