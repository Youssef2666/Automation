#!/usr/bin/env python
"""T06 - File Watcher: Process on Drop.

Local File Trigger on /home/node/.n8n-files/data/inbox (host: data/inbox/) -> read the file -> SHA-256
-> archive copy to MinIO artifacts/inbox/ -> CSV files are parsed and counted -> documents row
-> JSON receipt written to data/out/processed/. Nothing external; the folder is the API.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, catalog_id, code, cond_str, crypto_hash, extract, if_, manual_trigger,  # noqa: E402
                         postgres_insert, read_file, s3_upload, set_fields, write_file)

DESCRIBE_JS = r"""
// One item per dropped file. Works for the trigger output ({path, event}) and for the manual test item.
const p = String($json.path || $json.file || '');
const name = p.split('/').pop();
const ext = (name.includes('.') ? name.split('.').pop() : '').toLowerCase();
return [{ json: { path: p, file_name: name, ext, event: $json.event || 'manual', detected_at: new Date().toISOString() } }];
"""

RECEIPT_JS = r"""
// Build the receipt (JSON file) from what the run learned; the CSV row count is present only for CSV files.
const meta = $('Describe file').first().json;
const hash = $('SHA-256').first().json.hash;
let rows = null;
try { rows = $('Parse CSV').all().length; } catch (e) { rows = null; }
const bin = $('Read dropped file').first().binary?.data || {};
const receipt = { ...meta, sha256: hash, size: bin.fileSize || null, mime: bin.mimeType || null, csv_rows: rows,
                  archived_to: `s3://artifacts/inbox/${meta.file_name}`, processed_at: new Date().toISOString() };
const body = JSON.stringify(receipt, null, 2);
return [{ json: receipt, binary: { data: { data: Buffer.from(body, 'utf8').toString('base64'), mimeType: 'application/json', fileName: `${meta.file_name}.receipt.json` } } }];
"""


def build() -> Workflow:
    wf = Workflow("T06", "file-watcher", "File Watcher: Process on Drop", tags=["Triggers"],
                  error_workflow=catalog_id("P01"),
                  description="Watches data/inbox/; every dropped file is hashed, archived to MinIO, parsed if CSV, and receipted.")
    watch = wf.add("Watch data/inbox", "n8n-nodes-base.localFileTrigger", 1, {
        "triggerOn": "folder", "path": "/home/node/.n8n-files/data/inbox", "events": ["add"],
        "options": {"awaitWriteFinish": True, "ignoreInitial": True, "usePolling": True, "ignored": "**/.gitkeep"},
    })
    manual = manual_trigger(wf, "Run once (manual / CLI)")
    sample = set_fields(wf, "Sample file (manual runs)", {"path": "/home/node/.n8n-files/data/inbox/sample-drop.csv", "event": "manual"})
    desc = code(wf, "Describe file", DESCRIBE_JS)
    rd = read_file(wf, "Read dropped file", "={{ $json.path }}")
    sha = crypto_hash(wf, "SHA-256", "={{ $binary.data.data }}", prop="hash")
    sha.parameters.update({"type": "SHA256", "binaryData": True, "binaryPropertyName": "data"})
    del sha.parameters["value"]
    archive = s3_upload(wf, "Archive to MinIO (artifacts)", "artifacts", "=inbox/{{ $('Describe file').item.json.file_name }}").retry(3, 1000)
    is_csv = if_(wf, "CSV?", [cond_str("={{ $('Describe file').item.json.ext }}", "equals", "csv")])
    parse = extract(wf, "Parse CSV", "csv", headerRow=True)
    parse.parameters["binaryPropertyName"] = "data"
    doc = postgres_insert(wf, "Record document", "documents", {
        "kind": "inbox-file",
        "file_name": "={{ $('Describe file').item.json.file_name }}",
        "storage_key": "=s3://artifacts/inbox/{{ $('Describe file').item.json.file_name }}",
        "meta": "={{ JSON.stringify({ ext: $('Describe file').item.json.ext, sha256: $('SHA-256').item.json.hash, event: $('Describe file').item.json.event }) }}",
    }).once()
    receipt = code(wf, "Build receipt", RECEIPT_JS)
    save = write_file(wf, "Write receipt to data/out/processed", "=/home/node/.n8n-files/data/out/processed-{{ $json.file_name }}.receipt.json")
    wf.chain(watch, desc)
    wf.chain(manual, sample, desc)
    # Every consumer of the file branches from "Read dropped file" (Crypto and S3 outputs carry no binary).
    # The hash branch records the document and writes the receipt; the other two branches are side effects.
    wf.chain(desc, rd, archive)
    wf.connect(rd, is_csv)
    wf.connect(is_csv, parse, out=0)
    wf.connect(rd, sha)
    wf.chain(sha, doc, receipt, save)
    wf.sticky(
        "## T06 - File watcher\n"
        "`Local File Trigger` (polling, `awaitWriteFinish`) on `data/inbox/`. Drop a file on the host, the workflow "
        "reads it, hashes it (SHA-256), archives it to MinIO `artifacts/inbox/`, parses CSVs, records a `documents` "
        "row and writes a JSON receipt to `data/out/`.\n\n"
        "Manual runs use the sample path in **Sample file (manual runs)**. The trigger only listens while the workflow is published.",
        pos=(-40, -330), width=620, height=220)
    return wf


if __name__ == "__main__":
    build().save()
