#!/usr/bin/env python
"""M04 - DB Threshold Alert.

Every 10 min (or manual) -> Thresholds (Set) -> one Postgres query with the current values (pending orders, oldest
pending age, open urgent tickets, open tickets past SLA) -> Code compares each metric with its limit -> If any
breach -> one item per breach -> Redis INCR `m04:alert:<metric>:<yyyyLLddHH>` (at most one e-mail per metric per
clock hour) -> Decide (first alert / suppressed) -> e-mail ops@lab.local (Mailpit) + notifications row, or
"Suppressed". Every path ends in P08 - Log execution (`info` when all within thresholds, `warning` on a breach).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, catalog_id, code, cond_bool, email, execute_workflow, if_, manual_trigger,  # noqa: E402
                         noop, postgres_insert, postgres_query, redis, schedule, set_fields, split_out)

METRICS_SQL = """select
  (select count(*) from orders where status = 'pending')::int as pending_orders,
  (select coalesce(round(extract(epoch from (now() - min(ordered_at))) / 3600), 0)
     from orders where status = 'pending')::int as pending_age_hours,
  (select count(*) from tickets
     where status in ('open', 'triaged', 'in_progress') and priority = 'urgent')::int as open_tickets_urgent,
  (select count(*) from tickets
     where status in ('open', 'triaged', 'in_progress') and sla_due_at < now())::int as sla_breached,
  now() as checked_at"""

COMPARE_JS = r"""
// One row of current values in, one item out: every metric with its limit, plus the list of breaches.
const v = $input.first().json;
const t = $('Thresholds').first().json;
const defs = [
  { metric: 'pending_orders',      label: 'Pending orders',               value: v.pending_orders,      threshold: t.pending_orders_max,      severity: 'warning' },
  { metric: 'pending_age_hours',   label: 'Oldest pending order (hours)', value: v.pending_age_hours,   threshold: t.pending_age_hours_max,   severity: 'warning' },
  { metric: 'open_tickets_urgent', label: 'Open urgent tickets',          value: v.open_tickets_urgent, threshold: t.open_tickets_urgent_max, severity: 'error' },
  { metric: 'sla_breached',        label: 'Open tickets past SLA',        value: v.sla_breached,        threshold: t.sla_breached_max,        severity: 'error' },
];
const metrics = defs.map(d => ({ ...d, value: Number(d.value ?? 0), threshold: Number(d.threshold),
                                 breached: Number(d.value ?? 0) > Number(d.threshold) }));
const breached = metrics.filter(m => m.breached);
const summary = metrics.map(m => `${m.metric} ${m.value}/${m.threshold}${m.breached ? '!' : ''}`).join(', ');
return [{ json: { checked_at: v.checked_at, any_breached: breached.length > 0, breached, metrics, summary } }];
"""

DECIDE_JS = r"""
// One item per breach came back from Redis as { "<key>": count }. count === 1 -> first alert this hour.
const breaches = $('One item per breach').all().map(i => i.json);
const counts = $input.all().map(i => Number(Object.values(i.json)[0] || 0));
const rows = breaches.map((b, i) => ({ ...b, count: counts[i] || 0 }));
const toAlert = rows.filter(r => r.count === 1);
const suppressed = rows.filter(r => r.count !== 1);
const severity = toAlert.some(r => r.severity === 'error') ? 'error' : 'warning';
const line = (r) => `${r.metric} ${r.value} > ${r.threshold}`;
const tr = (r) => `<tr><td style="padding:4px 10px;border-bottom:1px solid #eee">${r.label}</td>` +
  `<td style="padding:4px 10px;border-bottom:1px solid #eee;text-align:right"><b>${r.value}</b></td>` +
  `<td style="padding:4px 10px;border-bottom:1px solid #eee;text-align:right">${r.threshold}</td>` +
  `<td style="padding:4px 10px;border-bottom:1px solid #eee">${r.severity}</td></tr>`;
const table = (list) => `<table style="border-collapse:collapse;font-size:14px"><tr>` +
  ['Metric', 'Value', 'Limit', 'Severity'].map(h => `<th align="left" style="padding:4px 10px;border-bottom:2px solid #333">${h}</th>`).join('') +
  `</tr>${list.map(tr).join('')}</table>`;
const hour = $('Thresholds').first().json.window_key;
const subject = `[Automation Lab] ${severity.toUpperCase()}: ${toAlert.length} metric${toAlert.length === 1 ? '' : 's'} over threshold (${toAlert.map(line).join(', ')})`;
const html = `<div style="font-family:sans-serif;max-width:720px">
<h2>DB threshold alert</h2>
<p>Checked ${$('Compare with thresholds').first().json.checked_at}. Source: demo database (orders, tickets).</p>
${table(toAlert)}
${suppressed.length ? `<p style="color:#888">Also over threshold, already alerted this hour (${hour}): ${suppressed.map(line).join(', ')}</p>` : ''}
<p style="color:#888;font-size:12px">One e-mail per metric per hour. Sent by M04 - DB Threshold Alert (Automation Lab).</p>
</div>`;
const text = [`DB threshold alert (${severity})`, ...toAlert.map(r => `  ${r.label}: ${r.value} (limit ${r.threshold}, ${r.severity})`),
  ...(suppressed.length ? [`  already alerted this hour: ${suppressed.map(line).join(', ')}`] : [])].join('\n');
return [{ json: {
  alert: toAlert.length > 0, severity, subject, html, text,
  alerted: toAlert.map(r => r.metric), suppressed: suppressed.map(r => r.metric),
  notes: toAlert.length
    ? `alerted: ${toAlert.map(line).join(', ')}` + (suppressed.length ? `; suppressed: ${suppressed.map(line).join(', ')}` : '')
    : `breach persists, alert suppressed this hour: ${suppressed.map(line).join(', ')}`,
} }];
"""


def log_input(wf: Workflow, name: str, status: str, notes: str):
    return set_fields(wf, name, {
        "execution_id": "={{ $execution.id }}",
        "workflow_id": "={{ $workflow.id }}",
        "workflow_name": "={{ $workflow.name }}",
        "status": status,
        "started_at": "={{ $('Thresholds').first().json.started_at }}",
        "notes": notes,
    })


def build() -> Workflow:
    wf = Workflow("M04", "db-threshold-alert", "DB Threshold Alert", tags=["Monitoring"],
                  error_workflow=catalog_id("P01"),
                  description="Checks four business metrics against limits every 10 minutes; e-mails once per "
                              "metric per hour and logs every run through P08.")
    trg = schedule(wf, "Every 10 minutes", minutes=10)
    manual = manual_trigger(wf, "Run once (manual / CLI)")
    cfg = set_fields(wf, "Thresholds", {
        "started_at": "={{ $now.toISO() }}",
        "window_key": "={{ $now.toFormat('yyyyLLddHH') }}",
        "pending_orders_max": 10,
        "pending_age_hours_max": 48,
        "open_tickets_urgent_max": 3,
        "sla_breached_max": 0,
        "alert_to": "ops@lab.local",
    })
    cfg.note("Limits live here, not in the SQL. window_key = current hour -> one alert per metric per hour.")
    current = postgres_query(wf, "Current values", METRICS_SQL).once().retry(3, 1000)
    compare = code(wf, "Compare with thresholds", COMPARE_JS)
    any_ = if_(wf, "Any breach?", [cond_bool("={{ $json.any_breached }}")])
    per = split_out(wf, "One item per breach", "breached")
    guard = redis(wf, "Alert-storm guard (INCR)", "incr",
                  "=m04:alert:{{ $json.metric }}:{{ $('Thresholds').first().json.window_key }}", ttl=3600).retry(3, 500)
    decide = code(wf, "Decide", DECIDE_JS)
    first = if_(wf, "First alert this hour?", [cond_bool("={{ $json.alert }}")])
    mail = email(wf, "Email ops (Mailpit)", "={{ $('Thresholds').first().json.alert_to }}", "={{ $json.subject }}",
                 html="={{ $json.html }}").retry(3, 2000)
    note = postgres_insert(wf, "Record notification", "notifications", {
        "channel": "email",
        "target": "={{ $('Thresholds').first().json.alert_to }}",
        "subject": "={{ $('Decide').first().json.subject }}",
        "body": "={{ $('Decide').first().json.text }}",
        "severity": "={{ $('Decide').first().json.severity }}",
        "sent_at": "={{ $now.toISO() }}",
    }).on_error("continueRegularOutput")
    suppressed = noop(wf, "Suppressed (already alerted this hour)")
    ok = noop(wf, "All within thresholds")
    log_alerted = log_input(wf, "Log input (alerted)", "warning", "={{ $('Decide').first().json.notes }}")
    log_suppressed = log_input(wf, "Log input (suppressed)", "warning", "={{ $('Decide').first().json.notes }}")
    log_ok = log_input(wf, "Log input (all within)", "info",
                       "={{ 'all within thresholds: ' + $('Compare with thresholds').first().json.summary }}")
    log = execute_workflow(wf, "Log execution (P08)", catalog_id("P08"), cached_name="P08 - Log execution")

    wf.chain(trg, cfg, current, compare, any_)
    wf.connect(manual, cfg)
    wf.connect(any_, per, out=0)
    wf.connect(any_, ok, out=1)
    wf.chain(per, guard, decide, first)
    wf.connect(first, mail, out=0)
    wf.connect(first, suppressed, out=1)
    wf.chain(mail, note, log_alerted, log)
    wf.chain(suppressed, log_suppressed, log)
    wf.chain(ok, log_ok, log)
    wf.sticky(
        "## M04 - DB threshold alert\n"
        "Every 10 min: **Thresholds** (Set) -> one SQL with the current values -> compare -> breaches only.\n\n"
        "Storm guard: Redis `INCR m04:alert:<metric>:<hour>` (TTL 1 h) - the first breach of a metric in a clock hour "
        "e-mails `ops@lab.local` (Mailpit) + `notifications` row; repeats are **Suppressed**.\n\n"
        "Every path ends in **P08 - Log execution**: `info` (all within), `warning` (alerted / suppressed), so a "
        "quiet run still leaves a row. Seed data trips pending_orders, pending_age_hours and sla_breached.",
        pos=(-40, -330), width=700, height=250)
    return wf


if __name__ == "__main__":
    build().save()
