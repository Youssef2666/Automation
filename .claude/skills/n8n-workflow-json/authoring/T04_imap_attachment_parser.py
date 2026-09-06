#!/usr/bin/env python
"""T04 - IMAP Email Trigger to Attachment Parser.

Email Trigger (IMAP, GreenMail inbox@lab.local) -> list attachments (Code) -> CSV attachments are parsed and
counted, every attachment is archived to MinIO artifacts/mail/ -> documents row per attachment -> acknowledgement
e-mail to the sender (Mailpit). test/send-test-email.py injects a message with a CSV through GreenMail's SMTP port.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, catalog_id, code, cond_str, email, extract, if_, imap_trigger, noop,  # noqa: E402
                         postgres_insert, s3_upload)

LIST_JS = r"""
// One item per e-mail (format: resolved). Emit one item per attachment, moving that attachment to binary "data".
const out = [];
for (const it of $input.all()) {
  const m = it.json;
  const bins = it.binary || {};
  const keys = Object.keys(bins).filter(k => k.startsWith('attachment_'));
  const from = (m.from && (m.from.text || (m.from.value && m.from.value[0] && m.from.value[0].address))) || m.from || '';
  const fromAddr = String(from).match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+/)?.[0] || String(from);
  if (!keys.length) {
    out.push({ json: { subject: m.subject, from: fromAddr, message_id: m.messageId, attachment: null, ext: null, has_attachment: false } });
    continue;
  }
  for (const k of keys) {
    const b = bins[k];
    const name = b.fileName || k;
    out.push({ json: { subject: m.subject, from: fromAddr, message_id: m.messageId, attachment: name,
                       ext: (name.includes('.') ? name.split('.').pop() : '').toLowerCase(), mime: b.mimeType,
                       size: b.fileSize || null, has_attachment: true, received_at: m.date || new Date().toISOString() },
               binary: { data: b } });
  }
}
return out;
"""

SUMMARY_JS = r"""
// Runs once after the CSV rows were parsed: count them for the documents row (0 when the attachment is not a CSV).
const att = $('List attachments').first().json;
let rows = 0;
try { rows = $('Parse CSV').all().length; } catch (e) { rows = 0; }
return [{ json: { ...att, csv_rows: rows } }];
"""


def build() -> Workflow:
    wf = Workflow("T04", "imap-attachment-parser", "IMAP Email Trigger to Attachment Parser", tags=["Triggers"],
                  error_workflow=catalog_id("P01"),
                  description="Polls a local IMAP inbox (GreenMail); attachments are parsed, archived and acknowledged.")
    trg = imap_trigger(wf, "Email Trigger (IMAP - GreenMail)")
    lst = code(wf, "List attachments", LIST_JS)
    has = if_(wf, "Has attachment?", [cond_str("={{ String($json.has_attachment) }}", "equals", "true")])
    none = noop(wf, "No attachment - ignore")
    archive = s3_upload(wf, "Archive to MinIO (artifacts)", "artifacts",
                        "=mail/{{ $('List attachments').item.json.attachment }}").retry(3, 1000)
    is_csv = if_(wf, "CSV?", [cond_str("={{ $('List attachments').item.json.ext }}", "equals", "csv")])
    parse = extract(wf, "Parse CSV", "csv", headerRow=True)
    summary = code(wf, "Summarize attachment", SUMMARY_JS)
    doc = postgres_insert(wf, "Record document", "documents", {
        "kind": "email-attachment",
        "file_name": "={{ $json.attachment }}",
        "storage_key": "=s3://artifacts/mail/{{ $json.attachment }}",
        "meta": "={{ JSON.stringify({ from: $json.from, subject: $json.subject, message_id: $json.message_id, mime: $json.mime, csv_rows: $json.csv_rows }) }}",
    })
    ack = email(wf, "Acknowledge sender (Mailpit)", "={{ $('Summarize attachment').item.json.from }}",
                "=Re: {{ $('Summarize attachment').item.json.subject }} - received",
                html="=<p>We received <b>{{ $('Summarize attachment').item.json.attachment }}</b>"
                     "{{ $('Summarize attachment').item.json.csv_rows ? ' (' + $('Summarize attachment').item.json.csv_rows + ' rows parsed)' : '' }} "
                     "and archived it. Reference: {{ $('Summarize attachment').item.json.message_id }}</p><p>- Automation Lab (T04)</p>").retry(3, 2000)
    wf.chain(trg, lst, has)
    wf.connect(has, archive, out=0)
    wf.connect(has, none, out=1)
    wf.connect(has, is_csv, out=0)
    wf.connect(is_csv, parse, out=0)
    wf.connect(is_csv, summary, out=1)
    wf.chain(parse, summary)
    summary.once()
    wf.chain(summary, doc, ack)
    wf.sticky(
        "## T04 - IMAP trigger -> attachment parser\n"
        "GreenMail serves IMAP on `greenmail:3143` (user `inbox@lab.local`); `test/send-test-email.py` pushes a "
        "message with a CSV through its SMTP port 3025. The trigger polls for UNSEEN mail, marks it read, and "
        "delivers attachments as binary `attachment_N`.\n\n"
        "Each attachment: archived to MinIO `artifacts/mail/`, parsed when CSV, recorded in `documents`, "
        "acknowledged to the sender via Mailpit. Mail without attachments is ignored.",
        pos=(-40, -330), width=640, height=230)
    return wf


if __name__ == "__main__":
    build().save()
