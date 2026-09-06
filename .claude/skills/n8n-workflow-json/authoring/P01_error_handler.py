#!/usr/bin/env python
"""P01 - Global Error Handler.

Error Trigger -> normalise the error context -> write execution_log -> alert-storm guard (Redis INCR, 10 min TTL)
-> e-mail via Mailpit + notifications row (first alert only; repeats are logged but suppressed).

Every other workflow in the lab sets `settings.errorWorkflow = wf_id("P01", "error-handler")`.
Also writes test/failing-caller.json: a two-node webhook workflow that always fails and points at P01. Import it,
activate it and `curl -X POST localhost:5678/webhook/p01-fail` to prove the handler live (CLI/manual executions do
not trigger error workflows, webhook executions do).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, cond_num, code, email, error_trigger, if_, noop, postgres_insert,  # noqa: E402
                         redis, stop_error, webhook, wf_id)

P01_ID = wf_id("P01", "error-handler")

NORMALISE_JS = r"""
// Error Trigger emits one item. Two shapes exist: a node failed inside an execution (execution.*) or a trigger
// failed to start one (trigger.*). Normalise both into a flat record that every downstream node can use.
const src = $input.first().json;
const exec = src.execution || {};
const trig = src.trigger || {};
const wfl = src.workflow || {};
const err = exec.error || trig.error || {};
const message = String(err.message || err.description || 'unknown error').slice(0, 500);
const stack = String(err.stack || '').split('\n').slice(0, 12).join('\n');
const node = exec.lastNodeExecuted || (err.node && err.node.name) || (trig.mode ? 'trigger' : 'unknown');
const startedAt = exec.startedAt || trig.startedAt || null;
const now = new Date();
const severity = /ECONNREFUSED|ETIMEDOUT|429|503|timeout/i.test(message) ? 'warning' : 'error';
return [{ json: {
  execution_id: String(exec.id || trig.id || `trigger-${now.getTime()}`),
  execution_url: exec.url || '',
  execution_mode: exec.mode || trig.mode || 'unknown',
  workflow_id: String(wfl.id || ''),
  workflow_name: wfl.name || 'unknown workflow',
  error_message: message,
  error_node: String(node),
  error_stack: stack,
  started_at: startedAt,
  failed_at: now.toISOString(),
  severity,
  subject: `[Automation Lab] ${severity.toUpperCase()} in ${wfl.name || 'workflow'} at node "${node}"`,
}}];
"""

EMAIL_HTML = """=<h2 style="font-family:sans-serif">{{ $('Normalize error').item.json.subject }}</h2>
<table style="font-family:sans-serif;border-collapse:collapse" cellpadding="6">
<tr><td><b>Workflow</b></td><td>{{ $('Normalize error').item.json.workflow_name }} ({{ $('Normalize error').item.json.workflow_id }})</td></tr>
<tr><td><b>Execution</b></td><td><a href="{{ $('Normalize error').item.json.execution_url }}">{{ $('Normalize error').item.json.execution_id }}</a> · mode {{ $('Normalize error').item.json.execution_mode }}</td></tr>
<tr><td><b>Failed node</b></td><td>{{ $('Normalize error').item.json.error_node }}</td></tr>
<tr><td><b>When</b></td><td>{{ $('Normalize error').item.json.failed_at }}</td></tr>
<tr><td><b>Message</b></td><td><code>{{ $('Normalize error').item.json.error_message }}</code></td></tr>
</table>
<pre style="background:#f4f4f4;padding:8px">{{ $('Normalize error').item.json.error_stack }}</pre>
<p style="color:#888">Further failures of this workflow are logged to execution_log but not e-mailed for 10 minutes (alert-storm guard).</p>"""


def build_handler() -> Workflow:
    wf = Workflow("P01", "error-handler", "Global Error Handler", tags=["pattern", "P01"],
                  description="Catches failures from every workflow, logs them and alerts once per 10 minutes.")
    trg = error_trigger(wf)
    norm = code(wf, "Normalize error", NORMALISE_JS)
    log = postgres_insert(wf, "Log to execution_log", "execution_log", {
        "execution_id": "={{ $json.execution_id }}",
        "workflow_id": "={{ $json.workflow_id }}",
        "workflow_name": "={{ $json.workflow_name }}",
        "status": "error",
        "started_at": "={{ $json.started_at }}",
        "finished_at": "={{ $json.failed_at }}",
        "error_message": "={{ $json.error_message }}",
        "error_node": "={{ $json.error_node }}",
    }).on_error("continueRegularOutput").retry(3, 2000)
    log.note("Logging must never block the alert: continue on error.")
    guard = redis(wf, "Alert-storm guard (INCR)", "incr",
                  "=p01:alerts:{{ $('Normalize error').item.json.workflow_id || 'unknown' }}", ttl=600).retry(3, 1000)
    first = if_(wf, "First alert in 10 min?", [cond_num("={{ Number(Object.values($json)[0]) }}", "equals", 1)])
    mail = email(wf, "Email ops (Mailpit)", "ops@lab.local", "={{ $('Normalize error').item.json.subject }}",
                 html=EMAIL_HTML).retry(3, 2000)
    note = postgres_insert(wf, "Record notification", "notifications", {
        "channel": "email",
        "target": "ops@lab.local",
        "subject": "={{ $('Normalize error').item.json.subject }}",
        "body": "={{ $('Normalize error').item.json.error_message }}",
        "severity": "={{ $('Normalize error').item.json.severity }}",
        "sent_at": "={{ $now.toISO() }}",
    }).on_error("continueRegularOutput")
    suppressed = noop(wf, "Suppressed (storm)")
    wf.chain(trg, norm, log, guard, first)
    wf.connect(first, mail, out=0)
    wf.connect(first, suppressed, out=1)
    wf.chain(mail, note)
    wf.sticky(
        "## P01 - Global Error Handler\n"
        "Set `Settings -> Error workflow` of every workflow to this one.\n\n"
        "1. **Normalize** both error shapes (node failure / trigger failure)\n"
        "2. **execution_log** row (never blocks the alert)\n"
        "3. **Redis INCR** per workflow, TTL 10 min: only the first failure e-mails\n"
        "4. **Mailpit** e-mail + `notifications` row\n\n"
        "Optional: add a Telegram node next to the e-mail (credential `Telegram - bot`).",
        pos=(-40, -330), width=560, height=260)
    return wf


def build_failing_caller() -> Workflow:
    wf = Workflow("P01", "failing-caller", "Failing caller (P01 test)", tags=["test"], error_workflow=P01_ID,
                  description="Always fails so that P01 fires. Test fixture only.")
    # A webhook (not a manual trigger): manual and CLI executions never reach the error workflow, a webhook does.
    t = webhook(wf, "POST /webhook/p01-fail", "p01-fail", response="onReceived")
    boom = stop_error(wf, "Always fails", "P01 test: simulated ECONNREFUSED talking to mock-api")
    wf.chain(t, boom)
    return wf


if __name__ == "__main__":
    handler = build_handler()
    handler.save()
    build_failing_caller().save(handler.folder() / "test" / "failing-caller.json")
    print("P01 id:", P01_ID)
