#!/usr/bin/env python
"""M01 - Uptime Monitor with Escalation.

Every minute: probe each target (HTTP, never throws) -> uptime_checks row per probe -> escalation state machine per
target in uptime_state (up -> warn after 1 failure -> down after 3 consecutive failures; recovery back to up)
-> alert e-mail on transitions to down / back to up, at most once per hour per target -> notifications row.
A deliberately failing target (mock-api /health/down) shows the escalation without breaking anything.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, aggregate, catalog_id, code, cond_num, email, http, if_, manual_trigger,  # noqa: E402
                         noop, postgres_insert, postgres_query, postgres_upsert, schedule, split_out)

TARGETS_JS = r"""
// Targets are in-network service URLs. `simulated-outage` always fails (mock-api /health/down) to demo escalation.
const targets = [
  { target: 'mock-api',         url: 'http://mock-api:8080/health' },
  { target: 'n8n',              url: 'http://n8n:5678/healthz' },
  { target: 'minio',            url: 'http://minio:9000/minio/health/live' },
  { target: 'simulated-outage', url: 'http://mock-api:8080/health/down' },
];
return targets.map(t => ({ json: { ...t, started_ms: Date.now() } }));
"""

EVALUATE_JS = r"""
// Per probe: HTTP output (full response, never error) or the error output (connection refused / timeout).
const t = $('Targets').item.json;
const r = $json;
const failed = r.error !== undefined && r.statusCode === undefined;
const status = failed ? null : Number(r.statusCode || 0);
const ok = !failed && status >= 200 && status < 400;
return { json: { target: t.target, url: t.url, status_code: status, ok,
                 latency_ms: Math.max(0, Date.now() - Number(t.started_ms || Date.now())),
                 error: failed ? String((r.error && r.error.message) || 'request failed') : null,
                 checked_at: new Date().toISOString() } };
"""

ESCALATE_JS = r"""
// Combine this run's probes with the persisted state -> new state per target + whether to alert.
const DOWN_AFTER = 3;            // consecutive failures before "down"
const ALERT_COOLDOWN_MS = 60 * 60 * 1000;
const probes = $('Collect probes').first().json.data || [];
const state = {};
for (const it of $('Load state').all()) state[it.json.target] = it.json;
const now = Date.now();
return probes.map(p => {
  const prev = state[p.target] || { state: 'up', failures: 0, since: null, last_alert_at: null };
  let failures = p.ok ? 0 : Number(prev.failures || 0) + 1;
  let next = p.ok ? 'up' : (failures >= DOWN_AFTER ? 'down' : 'warn');
  const changed = next !== prev.state;
  const transition = changed ? `${prev.state} -> ${next}` : null;
  const wantsAlert = (next === 'down' && prev.state !== 'down') || (next === 'up' && prev.state === 'down');
  const cooled = !prev.last_alert_at || (now - new Date(prev.last_alert_at).getTime()) > ALERT_COOLDOWN_MS;
  const alert = wantsAlert && cooled;
  return { json: { ...p, prev_state: prev.state, state: next, failures, transition, alert,
                   since: changed ? new Date().toISOString() : (prev.since || new Date().toISOString()),
                   last_alert_at: alert ? new Date().toISOString() : (prev.last_alert_at || null),
                   severity: next === 'down' ? 'error' : next === 'warn' ? 'warning' : 'info' } };
});
"""


def build() -> Workflow:
    wf = Workflow("M01", "uptime-monitor", "Uptime Monitor with Escalation", tags=["Monitoring"],
                  error_workflow=catalog_id("P01"),
                  description="Probes services every minute, keeps per-target state, escalates to down after 3 failures, alerts once per hour.")
    trg = schedule(wf, "Every minute", minutes=1)
    manual = manual_trigger(wf, "Run once (manual / CLI)")
    targets = code(wf, "Targets", TARGETS_JS)
    probe = http(wf, "Probe", "={{ $json.url }}", full_response=True, never_error=True, timeout_ms=5000,
                 response="autodetect").on_error("continueErrorOutput")
    evaluate = code(wf, "Evaluate probe", EVALUATE_JS, per_item=True)
    log = postgres_insert(wf, "Insert uptime_checks", "uptime_checks", {
        "target": "={{ $json.target }}", "url": "={{ $json.url }}", "status_code": "={{ $json.status_code }}",
        "latency_ms": "={{ $json.latency_ms }}", "ok": "={{ $json.ok }}", "checked_at": "={{ $json.checked_at }}",
    }).on_error("continueRegularOutput")
    collect = aggregate(wf, "Collect probes")
    load = postgres_query(wf, "Load state", "select target, state, failures, since, last_alert_at from uptime_state").always_output()
    escalate = code(wf, "Escalation logic", ESCALATE_JS)
    save = postgres_upsert(wf, "Upsert uptime_state", "uptime_state", ["target"], {
        "target": "={{ $json.target }}", "state": "={{ $json.state }}", "failures": "={{ $json.failures }}",
        "since": "={{ $json.since }}", "last_alert_at": "={{ $json.last_alert_at }}",
    })
    alerts = if_(wf, "Alert?", [cond_num("={{ $('Escalation logic').item.json.alert ? 1 : 0 }}", "equals", 1)])
    mail = email(wf, "Alert e-mail (Mailpit)", "ops@lab.local",
                 "=[Automation Lab] {{ $('Escalation logic').item.json.state.toUpperCase() }}: {{ $('Escalation logic').item.json.target }} ({{ $('Escalation logic').item.json.transition }})",
                 html="=<p><b>{{ $('Escalation logic').item.json.target }}</b> is now <b>{{ $('Escalation logic').item.json.state }}</b> "
                      "({{ $('Escalation logic').item.json.transition }}) after {{ $('Escalation logic').item.json.failures }} consecutive failure(s).</p>"
                      "<p>URL: {{ $('Escalation logic').item.json.url }}<br>Last status: {{ $('Escalation logic').item.json.status_code ?? $('Escalation logic').item.json.error }}</p>"
                      "<p>Next alert for this target no sooner than one hour from now.</p>").retry(3, 2000)
    note = postgres_insert(wf, "Record notification", "notifications", {
        "channel": "email", "target": "ops@lab.local",
        "subject": "={{ $('Escalation logic').item.json.state.toUpperCase() }}: {{ $('Escalation logic').item.json.target }}",
        "body": "={{ $('Escalation logic').item.json.transition }}",
        "severity": "={{ $('Escalation logic').item.json.severity }}", "sent_at": "={{ $now.toISO() }}",
    }).on_error("continueRegularOutput")
    quiet = noop(wf, "No transition")
    wf.chain(trg, targets, probe)
    wf.connect(manual, targets)
    wf.connect(probe, evaluate, out=0)
    wf.connect(probe, evaluate, out=1)
    wf.chain(evaluate, log, collect, load, escalate, save, alerts)
    wf.connect(alerts, mail, out=0)
    wf.connect(alerts, quiet, out=1)
    wf.chain(mail, note)
    wf.sticky(
        "## M01 - Uptime monitor with escalation\n"
        "Probes 4 targets every minute (one is a **simulated outage**). Every probe is a row in `uptime_checks`; "
        "`uptime_state` holds the state machine per target: up -> warn (1st failure) -> down (3 consecutive) -> up on recovery.\n\n"
        "Alerts only on transitions to down / back to up, and at most once per hour per target (no alert storms). "
        "Latency is measured around the HTTP node (approximate).",
        pos=(-40, -330), width=640, height=230)
    return wf


if __name__ == "__main__":
    build().save()
