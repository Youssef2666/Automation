#!/usr/bin/env python
"""T05 - Form Trigger to Record and Confirmation Email.

n8n Form (/form/t05-contact) -> normalise -> upsert leads (by e-mail) -> confirmation e-mail to the submitter
-> notifications row. The form itself is served by n8n; nothing external.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import Workflow, catalog_id, code, email, form_trigger, postgres_insert, postgres_upsert  # noqa: E402

FIELDS = [
    {"fieldLabel": "Name", "placeholder": "Your full name", "requiredField": True},
    {"fieldLabel": "Email", "fieldType": "email", "placeholder": "you@lab.local", "requiredField": True},
    {"fieldLabel": "Company", "placeholder": "Company (optional)"},
    {"fieldLabel": "Topic", "fieldType": "dropdown",
     "fieldOptions": {"values": [{"option": "Demo request"}, {"option": "Support"}, {"option": "Partnership"}, {"option": "Other"}]},
     "requiredField": True},
    {"fieldLabel": "Message", "fieldType": "textarea", "placeholder": "How can we help?"},
]

NORMALISE_JS = r"""
// Form submissions arrive keyed by field label. Trim, lower-case the e-mail, derive the company domain, score.
const f = $input.first().json;
const email = String(f.Email || '').trim().toLowerCase();
const company = String(f.Company || '').trim();
const topic = String(f.Topic || 'Other');
const domain = email.includes('@') ? email.split('@')[1] : null;
const score = { 'Demo request': 60, 'Partnership': 50, 'Support': 20, 'Other': 10 }[topic] ?? 10;
return [{ json: {
  name: String(f.Name || '').trim(), email, company: company || null, domain, topic,
  message: String(f.Message || '').trim(), score, source: 'form', submitted_at: f.submittedAt || new Date().toISOString(),
  enriched: JSON.stringify({ topic, message: String(f.Message || '').trim(), form: 't05-contact', submitted_at: f.submittedAt || null }),
}}];
"""


def build() -> Workflow:
    wf = Workflow("T05", "form-to-record", "Form Trigger to Record and Confirmation Email", tags=["Triggers"],
                  error_workflow=catalog_id("P01"),
                  description="Hosted n8n form -> leads table -> confirmation e-mail.")
    form = form_trigger(wf, "Contact form", "t05-contact", "Automation Lab - contact us", FIELDS,
                        description="Synthetic demo form. Submissions land in the demo database and trigger a confirmation e-mail.",
                        button="Send")
    norm = code(wf, "Normalize submission", NORMALISE_JS)
    up = postgres_upsert(wf, "Upsert lead", "leads", ["email"], {
        "email": "={{ $json.email }}",
        "name": "={{ $json.name }}",
        "company": "={{ $json.company }}",
        "domain": "={{ $json.domain }}",
        "source": "form",
        "score": "={{ $json.score }}",
        "status": "new",
        "enriched": "={{ $json.enriched }}",
    }).retry(3, 1000)
    confirm = email(wf, "Confirmation e-mail (Mailpit)", "={{ $('Normalize submission').item.json.email }}",
                    "=We received your message - {{ $('Normalize submission').item.json.topic }}",
                    html="=<p>Hi {{ $('Normalize submission').item.json.name }},</p>"
                         "<p>thanks for contacting Automation Lab about <b>{{ $('Normalize submission').item.json.topic }}</b>. "
                         "We will get back to you within one business day.</p>"
                         "<blockquote>{{ $('Normalize submission').item.json.message || '(no message)' }}</blockquote>"
                         "<p>- Automation Lab (T05 demo)</p>").retry(3, 2000)
    note = postgres_insert(wf, "Record notification", "notifications", {
        "channel": "email",
        "target": "={{ $('Normalize submission').item.json.email }}",
        "subject": "=We received your message - {{ $('Normalize submission').item.json.topic }}",
        "body": "=T05 confirmation for lead {{ $('Normalize submission').item.json.email }}",
        "severity": "info",
        "sent_at": "={{ $now.toISO() }}",
    }).on_error("continueRegularOutput")
    wf.chain(form, norm, up, confirm, note)
    wf.sticky(
        "## T05 - Form -> record -> confirmation\n"
        "The form is hosted by n8n at `/form/t05-contact` (published workflow). Submissions arrive keyed by field "
        "label; the Code node normalises them and scores the lead by topic.\n\n"
        "`leads` is upserted on e-mail (a second submission updates, never duplicates), the submitter gets a "
        "confirmation via Mailpit, and `notifications` records what was sent.",
        pos=(-40, -330), width=620, height=210)
    return wf


if __name__ == "__main__":
    build().save()
