#!/usr/bin/env python
"""A02 - Ticket/e-mail classification and routing.

Hourly (or manual) -> Triage settings (Set) -> oldest open + unassigned rows from `tickets` -> Loop (one ticket per
call) -> Basic LLM Chain with a Structured Output Parser on a local Ollama model -> Validate classification (Code:
the parser guarantees the shape, the Code node checks the *values* against the table's enums) -> Decide routing ->
only a parsed, valid, confident classification updates the ticket (category, priority, status = triaged,
assigned_to = queue mailbox) and e-mails the queue; everything else leaves the row untouched and goes to the
triage desk. The loop's done branch summarises the run and logs it once through P08.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, catalog_id, code, cond_bool, cond_exists, email, execute_workflow,  # noqa: E402
                         if_, llm_chain, loop, manual_trigger, ollama_chat, output_parser, postgres_insert,
                         postgres_query, schedule, set_fields)

# --- SQL ------------------------------------------------------------------------------------------
SELECT_SQL = """select t.id,
       t.ticket_no,
       t.channel,
       t.subject,
       t.body,
       t.category   as seed_category,
       t.priority   as seed_priority,
       t.created_at,
       c.name       as customer_name,
       c.email      as customer_email
  from tickets t
  left join customers c on c.id = t.customer_id
 where t.status = 'open'
   and t.assigned_to is null
 order by t.created_at asc
 limit $1"""

UPDATE_SQL = """update tickets
   set category    = $2,
       priority    = $3,
       status      = 'triaged',
       assigned_to = $4
 where id = $1
   and status = 'open'
returning id, ticket_no, category, priority, status, assigned_to"""

# --- prompt ---------------------------------------------------------------------------------------
SYSTEM = (
    "You are the triage assistant of a small B2B support desk. You classify exactly one support ticket.\n"
    "category - choose one: billing | technical | account | shipping | other.\n"
    "  billing   invoices, charges, refunds, VAT, billing address, pricing on an existing invoice\n"
    "  technical errors, API failures, bugs, failed exports, delayed webhooks, the product not working\n"
    "  account   account ownership, adding or removing users, access, data deletion, two-factor codes\n"
    "  shipping  deliveries, tracking numbers, damaged or missing parcels, delivery addresses\n"
    "  other     anything else: feature requests, bulk pricing questions, partnerships, feedback\n"
    "priority - choose one: low | normal | high | urgent, from the business impact stated in the text.\n"
    "  urgent    money already lost or a full outage; high  blocked work; normal  the default; low  nice to have\n"
    "confidence - a number between 0 and 1, your own confidence in the category.\n"
    "reason - one short English sentence (max 20 words) naming the words that decided it.\n"
    "Tickets may be written in English or Arabic; classify Arabic tickets the same way and always answer in "
    "English. Answer with JSON only: no prose, no markdown, no code fence.\n"
    'Return one JSON object with exactly one key, "output", holding the four fields: '
    '{"output": {"category": "...", "priority": "...", "confidence": 0.0, "reason": "..."}}. '
    "Nothing outside that object."
)

SCHEMA_EXAMPLE = {
    "category": "billing",
    "priority": "high",
    "confidence": 0.82,
    "reason": "Mentions being charged twice for one invoice.",
}

# --- Code nodes -----------------------------------------------------------------------------------
PREPARE_JS = r"""
// One ticket per loop iteration. Build the text the model sees and keep every field the rest of the run needs.
const t = $input.first().json;
const clean = (s) => String(s ?? '').replace(/\s+/g, ' ').trim();
const subject = clean(t.subject);
const body = clean(t.body).slice(0, 1200);
return [{ json: {
  ticket_id: t.id,
  ticket_no: t.ticket_no,
  channel: t.channel,
  subject,
  body,
  seed_category: t.seed_category ?? null,
  seed_priority: t.seed_priority ?? null,
  customer_name: t.customer_name ?? null,
  customer_email: t.customer_email ?? null,
  created_at: t.created_at ?? null,
  prompt: [
    'Ticket: ' + t.ticket_no,
    'Channel: ' + t.channel,
    'Subject: ' + (subject || '(no subject)'),
    'Message: ' + (body || '(empty)'),
  ].join('\n'),
} }];
"""

VALIDATE_JS = r"""
// The Structured Output Parser guarantees the JSON *shape*; it knows nothing about our enums. Check the values
// here, before anything touches the database. Invalid values are data, not exceptions: they become problems[].
const CATEGORIES = ['billing', 'technical', 'account', 'shipping', 'other'];
const PRIORITIES = ['low', 'normal', 'high', 'urgent'];
const t = $('Prepare prompt').last().json;
const raw = $input.first().json.output ?? $input.first().json ?? {};
const norm = (v) => String(v ?? '').trim().toLowerCase();
const problems = [];

let category = norm(raw.category);
if (!CATEGORIES.includes(category)) {
  problems.push('category ' + JSON.stringify(raw.category ?? null) + ' is not one of ' + CATEGORIES.join('|'));
  category = null;
}
let priority = norm(raw.priority);
if (!PRIORITIES.includes(priority)) {
  problems.push('priority ' + JSON.stringify(raw.priority ?? null) + ' is not one of ' + PRIORITIES.join('|'));
  priority = null;
}
let confidence = Number(raw.confidence);
if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1) {
  problems.push('confidence ' + JSON.stringify(raw.confidence ?? null) + ' is not a number between 0 and 1');
  confidence = 0;
}
const reason = String(raw.reason ?? '').trim().slice(0, 240);
if (!reason) problems.push('reason is empty');

return [{ json: { ...t, parsed: true, valid: problems.length === 0, problems,
                  category, priority, confidence, reason, model_output: raw } }];
"""

UNUSABLE_JS = r"""
// Error output of the chain: the model returned something the parser could not turn into our schema, or Ollama
// was unreachable. The node already re-sampled once (retryOnFail). Nothing is written to the ticket.
const t = $('Prepare prompt').last().json;
const e = $input.first().json ?? {};
const err = e.error ?? e;
const message = String(err?.message ?? err?.description ?? err ?? 'unknown error').replace(/\s+/g, ' ').slice(0, 300);
return [{ json: { ...t, parsed: false, valid: false, problems: ['model output unusable: ' + message],
                  category: null, priority: null, confidence: 0, reason: '', model_output: null } }];
"""

DECIDE_JS = r"""
// One place decides what happens to this ticket, so the e-mail, the notification row and the summary agree.
const QUEUES = {
  billing: 'billing@lab.local',
  technical: 'support-tech@lab.local',
  account: 'accounts@lab.local',
  shipping: 'logistics@lab.local',
  other: 'support@lab.local',
};
const cfg = $('Triage settings').first().json;
const c = $input.first().json;
const minConfidence = Number(cfg.min_confidence);
const confidence = Number(c.confidence) || 0;
const auto_route = Boolean(c.valid) && confidence >= minConfidence;
const route_to = auto_route ? QUEUES[c.category] : cfg.review_to;
const problems = Array.isArray(c.problems) ? c.problems : [];
const review_reason = auto_route
  ? ''
  : (problems.length ? problems.join('; ')
                     : 'confidence ' + confidence.toFixed(2) + ' below min_confidence ' + minConfidence.toFixed(2));
const agreed_with_seed = Boolean(c.seed_category) && c.category === c.seed_category;

const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const row = (k, v) => '<tr><td style="padding:4px 10px;border-bottom:1px solid #eee;color:#666">' + esc(k) +
  '</td><td style="padding:4px 10px;border-bottom:1px solid #eee">' + esc(v) + '</td></tr>';
const subject = auto_route
  ? '[Automation Lab] ' + c.ticket_no + ' -> ' + c.category + ' queue (' + c.priority + ')'
  : '[Automation Lab] ' + c.ticket_no + ' needs manual triage';
const html = '<div style="font-family:sans-serif;max-width:720px">' +
  '<h2>' + esc(auto_route ? 'Ticket routed: ' + c.ticket_no : 'Ticket needs a human: ' + c.ticket_no) + '</h2>' +
  '<table style="border-collapse:collapse;font-size:14px">' +
  row('Subject', c.subject || '(no subject)') +
  row('Channel', c.channel) +
  row('Customer', [c.customer_name, c.customer_email].filter(Boolean).join(' - ') || 'unknown') +
  row('Model category', c.category ?? '(rejected)') +
  row('Model priority', c.priority ?? '(rejected)') +
  row('Confidence', confidence.toFixed(2) + ' (min ' + minConfidence.toFixed(2) + ')') +
  row('Model reason', c.reason || '(none)') +
  row('Label in the seed data', c.seed_category
    ? c.seed_category + (c.category ? (agreed_with_seed ? ' (agrees)' : ' (differs)') : '')
    : 'none') +
  (auto_route ? '' : row('Why a human', review_reason)) +
  '</table>' +
  '<p style="font-size:14px;white-space:pre-wrap;background:#fafafa;padding:10px;border-left:3px solid #ddd">' +
  esc(c.body || '(empty body)') + '</p>' +
  '<p style="color:#888;font-size:12px">' +
  (auto_route
    ? 'The ticket was set to status <b>triaged</b> and assigned to this queue by A02 - Ticket Classifier.'
    : 'The ticket row was <b>not</b> modified: A02 never writes a classification it cannot validate.') +
  ' Classified by a local Ollama model; treat the category as a suggestion.</p></div>';
const text = [
  (auto_route ? 'Routed ' : 'Needs manual triage: ') + c.ticket_no + ' (' + c.channel + ')',
  'Subject: ' + (c.subject || '(no subject)'),
  'Category: ' + (c.category ?? '(rejected)') + ' / priority: ' + (c.priority ?? '(rejected)') +
    ' / confidence: ' + confidence.toFixed(2),
  'Reason: ' + (c.reason || '(none)'),
  auto_route ? 'Assigned to: ' + route_to : 'Why a human: ' + review_reason,
].join('\n');

return [{ json: {
  ticket_id: c.ticket_id, ticket_no: c.ticket_no, subject: c.subject, channel: c.channel,
  seed_category: c.seed_category, agreed_with_seed,
  parsed: Boolean(c.parsed), valid: Boolean(c.valid), problems, review_reason,
  category: c.category, priority: c.priority, confidence, reason: c.reason,
  action: auto_route ? 'route' : 'review', auto_route, route_to,
  severity: auto_route ? 'info' : 'warning',
  subject_line: subject, html, text,
} }];
"""

SUMMARIZE_JS = r"""
// The loop's done branch carries one Outcome item per ticket (both lanes feed it back).
const rows = $input.all().map(i => i.json).filter(r => r && r.ticket_no);
const routed = rows.filter(r => r.action === 'route');
const review = rows.filter(r => r.action === 'review');
const unparsed = rows.filter(r => r.parsed === false);
const invalid = rows.filter(r => r.parsed === true && r.valid === false);
const lowConf = review.filter(r => r.parsed === true && r.valid === true);
// Agreement is only meaningful for tickets the model actually classified (the seed category is the label that
// produced the ticket text, so it doubles as a reference label - see the README).
const labelled = rows.filter(r => r.seed_category && r.valid === true);
const agreed = labelled.filter(r => r.agreed_with_seed === true);
const byCategory = {};
for (const r of routed) byCategory[r.category] = (byCategory[r.category] || 0) + 1;
const agreement = labelled.length ? Math.round((agreed.length / labelled.length) * 100) : null;
const notes = [
  'classified ' + rows.length + ' ticket(s): ' + routed.length + ' routed, ' + review.length + ' to the triage desk',
  unparsed.length ? unparsed.length + ' unparseable' : null,
  invalid.length ? invalid.length + ' invalid values' : null,
  lowConf.length ? lowConf.length + ' below min_confidence' : null,
  agreement === null ? null : 'category matched the seed label on ' + agreed.length + '/' + labelled.length +
    ' (' + agreement + '%)',
].filter(Boolean).join('; ');
return [{ json: {
  total: rows.length,
  routed: routed.length,
  needs_review: review.length,
  unparsed: unparsed.length,
  invalid: invalid.length,
  low_confidence: lowConf.length,
  seed_agreement_pct: agreement,
  by_category: byCategory,
  notes,
  tickets: rows.map(r => ({ ticket_no: r.ticket_no, action: r.action, category: r.category,
                            priority: r.priority, confidence: r.confidence, route_to: r.route_to,
                            seed_category: r.seed_category, agreed: r.agreed_with_seed,
                            problems: r.problems })),
} }];
"""


def build() -> Workflow:
    wf = Workflow("A02", "ticket-classifier", "Ticket Classification and Routing", tags=["AI"],
                  error_workflow=catalog_id("P01"),
                  description="Classifies open support tickets with a local Ollama model into a validated JSON "
                              "schema and routes them to a queue; anything unparseable, invalid or low-confidence "
                              "goes to a human and never touches the ticket row.")

    # --- trigger + settings ----------------------------------------------------------------------
    trg = schedule(wf, "Every hour", hours=1)
    manual = manual_trigger(wf, "Run once (manual / CLI)")
    cfg = set_fields(wf, "Triage settings", {
        "started_at": "={{ $now.toISO() }}",
        "batch_limit": 4,
        "min_confidence": 0.6,
        "review_to": "triage@lab.local",
    })
    cfg.note("How many tickets per run, and how sure the model has to be before the row is written.")

    # --- fetch -----------------------------------------------------------------------------------
    fetch = postgres_query(wf, "Open tickets (oldest first)", SELECT_SQL,
                           params="={{ [ $json.batch_limit ] }}").retry(3, 1000).always_output()
    any_ = if_(wf, "Any open tickets?", [cond_exists("={{ $json.id }}")])

    # --- classify one ticket at a time ------------------------------------------------------------
    lp = loop(wf, "Loop over tickets", batch_size=1)
    prep = code(wf, "Prepare prompt", PREPARE_JS)
    classify = llm_chain(wf, "Classify ticket (LLM)", "={{ $json.prompt }}", system=SYSTEM, parser=True)
    classify.retry(2, 2000).on_error("continueErrorOutput")
    classify.note("One item per call (batch size 1) so the error output is trustworthy; 2 tries = one re-sample.")
    model = ollama_chat(wf, "Ollama chat model", model="llama3.2:3b", temperature=0, json_mode=True)
    parser = output_parser(wf, "Classification schema", example=SCHEMA_EXAMPLE)
    wf.attach(model, classify, "ai_languageModel")
    wf.attach(parser, classify, "ai_outputParser")

    validate = code(wf, "Validate classification", VALIDATE_JS)
    unusable = code(wf, "Model output unusable", UNUSABLE_JS).at(1860, 460)
    decide = code(wf, "Decide routing", DECIDE_JS)
    confident = if_(wf, "Valid and confident?", [cond_bool("={{ $json.auto_route }}")])

    update = postgres_query(wf, "Triage ticket (Postgres)", UPDATE_SQL,
                            params="={{ [ $json.ticket_id, $json.category, $json.priority, $json.route_to ] }}")
    update.retry(3, 1000).always_output()
    update.note("Only reached by a validated classification; `and status = 'open'` keeps a re-run a no-op.")

    notify = email(wf, "Notify the queue (Mailpit)",
                   "={{ $('Decide routing').last().json.route_to }}",
                   "={{ $('Decide routing').last().json.subject_line }}",
                   html="={{ $('Decide routing').last().json.html }}").retry(3, 2000)
    record = postgres_insert(wf, "Record notification", "notifications", {
        "channel": "email",
        "target": "={{ $('Decide routing').last().json.route_to }}",
        "subject": "={{ $('Decide routing').last().json.subject_line }}",
        "body": "={{ $('Decide routing').last().json.text }}",
        "severity": "={{ $('Decide routing').last().json.severity }}",
        "sent_at": "={{ $now.toISO() }}",
    }).on_error("continueRegularOutput")
    outcome = set_fields(wf, "Outcome", {
        "ticket_id": ("={{ $('Decide routing').last().json.ticket_id }}", "number"),
        "ticket_no": "={{ $('Decide routing').last().json.ticket_no }}",
        "action": "={{ $('Decide routing').last().json.action }}",
        "category": "={{ $('Decide routing').last().json.category || '' }}",
        "priority": "={{ $('Decide routing').last().json.priority || '' }}",
        "confidence": ("={{ $('Decide routing').last().json.confidence }}", "number"),
        "route_to": "={{ $('Decide routing').last().json.route_to }}",
        "parsed": ("={{ $('Decide routing').last().json.parsed }}", "boolean"),
        "valid": ("={{ $('Decide routing').last().json.valid }}", "boolean"),
        "seed_category": "={{ $('Decide routing').last().json.seed_category || '' }}",
        "agreed_with_seed": ("={{ $('Decide routing').last().json.agreed_with_seed }}", "boolean"),
        "reason": "={{ $('Decide routing').last().json.reason || '' }}",
        "problems": "={{ $('Decide routing').last().json.problems.join('; ') }}",
    })

    # --- summary + one log row --------------------------------------------------------------------
    summary = code(wf, "Summarize run", SUMMARIZE_JS).at(3660, 340)
    log_done = set_fields(wf, "Log input (classified)", {
        "execution_id": "={{ $execution.id }}",
        "workflow_id": "={{ $workflow.id }}",
        "workflow_name": "={{ $workflow.name }}",
        "status": "={{ $json.needs_review > 0 ? 'warning' : 'success' }}",
        "started_at": "={{ $('Triage settings').first().json.started_at }}",
        "notes": "={{ $json.notes }}",
    }).at(3920, 340)
    log_none = set_fields(wf, "Log input (nothing to triage)", {
        "execution_id": "={{ $execution.id }}",
        "workflow_id": "={{ $workflow.id }}",
        "workflow_name": "={{ $workflow.name }}",
        "status": "info",
        "started_at": "={{ $('Triage settings').first().json.started_at }}",
        "notes": "no open unassigned tickets to classify",
    }).at(3920, 560)
    log = execute_workflow(wf, "Log execution (P08)", catalog_id("P08"),
                           cached_name="P08 - Log execution").at(4180, 450)

    # --- wiring ------------------------------------------------------------------------------------
    wf.chain(trg, cfg, fetch, any_)
    wf.connect(manual, cfg)
    wf.connect(any_, lp, out=0)
    wf.connect(any_, log_none, out=1)
    wf.connect(lp, summary, out=0)            # done
    wf.connect(lp, prep, out=1)               # loop body
    wf.chain(prep, classify, validate, decide, confident)
    wf.connect(classify, unusable, out=1)     # error output of the chain
    wf.connect(unusable, decide)
    wf.connect(confident, update, out=0)
    wf.connect(confident, notify, out=1)      # review lane: no database write
    wf.chain(update, notify, record, outcome)
    wf.connect(outcome, lp)                   # back into the loop
    wf.chain(summary, log_done, log)
    wf.connect(log_none, log)

    # --- documentation on the canvas ---------------------------------------------------------------
    wf.sticky(
        "## A02 - Ticket classification -> routing\n"
        "Hourly (or manual): the oldest **open, unassigned** tickets -> one LLM call per ticket on a local "
        "Ollama model (`llama3.2:3b`, `format: json`, temperature 0) through a **Structured Output Parser**.\n\n"
        "Only a parsed **and** valid classification at or above `min_confidence` writes to the row "
        "(`category`, `priority`, `status = triaged`, `assigned_to = <queue>`) and e-mails that queue. "
        "Everything else leaves the ticket untouched and goes to `triage@lab.local`: the workflow never "
        "stores a guess it cannot defend.\n\n"
        "Patterns: **P01** (error workflow), **P08** (exactly one `execution_log` row per run).",
        pos=(-60, -430), width=780, height=270)
    wf.sticky(
        "### Three ways a 3B model fails\n"
        "**Unparseable** - the chain node throws (`Failed to parse`); its **error output** feeds *Model output "
        "unusable*. `retryOnFail: 2` re-samples the model once before that.\n\n"
        "**Parseable but wrong** - `{\"category\": \"refund\"}` is valid JSON and invalid data; *Validate "
        "classification* checks the values against `billing|technical|account|shipping|other` and "
        "`low|normal|high|urgent`.\n\n"
        "**Right answer, wrong envelope** - the parser expects `{\"output\": {...}}` and silently strips "
        "anything else to `{}`. Measured on the four test tickets: 2/4 wrapped when only the parser asks, "
        "4/4 once the system message asks too - which is why it does.\n\n"
        "All three end as `action = review`: e-mail to the triage desk, ticket row untouched.",
        pos=(1480, -400), width=640, height=320, color=3)
    return wf


if __name__ == "__main__":
    build().save()
