#!/usr/bin/env python
"""M05 - Price / Exchange-rate Watcher.

Hourly (or manual) -> P02 GET http://mock-api:8080/rates/latest (USD base, 7 quotes) -> compare each quote with
the last stored rate in rate_history -> insert the new rates -> alert (e-mail + notifications) when a quote moved
more than `threshold_pct`, with a per-quote Redis cooldown (6 h) so a volatile pair does not spam.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, aggregate, catalog_id, code, cond_bool, cond_num, email, execute_workflow,  # noqa: E402
                         if_, manual_trigger, noop, postgres_insert, postgres_query, redis, schedule, set_fields,
                         split_out, stop_error)

COMPARE_JS = r"""
// Latest rates (P02 result) vs the last stored rate per quote.
const cfg = $('Config').first().json;
const res = $('Fetch rates (P02)').first().json;
const latest = (res.data && res.data.rates) || {};
const fetchedAt = (res.data && res.data.date) || new Date().toISOString();
const last = {};
for (const it of $input.all()) if (it.json.quote) last[it.json.quote] = Number(it.json.rate);
const watch = String(cfg.quotes || '').split(',').map(s => s.trim().toUpperCase()).filter(Boolean);
const out = [];
for (const q of watch) {
  const rate = Number(latest[q]);
  if (!Number.isFinite(rate)) continue;
  const prev = last[q];
  const change = prev ? ((rate - prev) / prev) * 100 : 0;
  out.push({ json: { base: 'USD', quote: q, rate, previous: prev ?? null,
                     change_pct: Math.round(change * 1000) / 1000, fetched_at: fetchedAt,
                     alert: prev !== undefined && Math.abs(change) >= Number(cfg.threshold_pct),
                     direction: change > 0 ? 'up' : change < 0 ? 'down' : 'flat' } });
}
return out;
"""

ALERT_HTML = """=<h3 style="font-family:sans-serif">Exchange-rate alert (USD base)</h3>
<table style="font-family:sans-serif;border-collapse:collapse" cellpadding="6">
<tr><th align="left">Pair</th><th align="right">Previous</th><th align="right">Now</th><th align="right">Change</th></tr>
{{ $json.data.map(r => '<tr><td>USD/' + r.quote + '</td><td align="right">' + r.previous + '</td><td align="right">' + r.rate + '</td><td align="right">' + r.change_pct + '%</td></tr>').join('') }}
</table>
<p style="color:#888">Threshold {{ $('Config').first().json.threshold_pct }}%. Next alert per pair no sooner than 6 h. M05 - Automation Lab.</p>"""


def build() -> Workflow:
    wf = Workflow("M05", "exchange-rate-watcher", "Price / Exchange-rate Watcher", tags=["Monitoring"],
                  error_workflow=catalog_id("P01"),
                  description="Hourly rate fetch through P02, history in Postgres, threshold alerts with per-pair cooldown.")
    trg = schedule(wf, "Every hour", hours=1)
    manual = manual_trigger(wf, "Run once (manual / CLI)")
    cfg = set_fields(wf, "Config", {"quotes": "EUR, GBP, LYD, EGP, TRY, AED, SAR", "threshold_pct": 0.5,
                                    "report_to": "finance@lab.local"})
    req = set_fields(wf, "Rates request", {"url": "http://mock-api:8080/rates/latest", "max_attempts": 4, "base_ms": 500})
    fetch = execute_workflow(wf, "Fetch rates (P02)", catalog_id("P02"), cached_name="P02 - HTTP with backoff")
    ok = if_(wf, "Fetched OK?", [cond_bool("={{ $json.ok }}")])
    fail = stop_error(wf, "Rates unavailable", "=M05: rate provider failed after {{ $json.attempts }} attempt(s): {{ $json.response }}")
    last = postgres_query(wf, "Last stored rate per quote",
                          "select distinct on (quote) quote, rate::float as rate, fetched_at from rate_history "
                          "where base = 'USD' order by quote, fetched_at desc").always_output()
    compare = code(wf, "Compare with history", COMPARE_JS)
    store = postgres_insert(wf, "Insert rate_history", "rate_history", {
        "base": "={{ $json.base }}", "quote": "={{ $json.quote }}", "rate": "={{ $json.rate }}",
        "fetched_at": "={{ $now.toISO() }}",
    }).on_error("continueRegularOutput")
    moved = if_(wf, "Moved beyond threshold?", [cond_bool("={{ $('Compare with history').item.json.alert }}")])
    cool = redis(wf, "Cooldown per pair (INCR)", "incr", "=m05:alert:{{ $('Compare with history').item.json.quote }}", ttl=21600)
    first = if_(wf, "First alert in 6 h?", [cond_num("={{ Number(Object.values($json)[0]) }}", "equals", 1)])
    keep = set_fields(wf, "Alert row", {
        "quote": "={{ $('Compare with history').item.json.quote }}",
        "rate": ("={{ $('Compare with history').item.json.rate }}", "number"),
        "previous": ("={{ $('Compare with history').item.json.previous }}", "number"),
        "change_pct": ("={{ $('Compare with history').item.json.change_pct }}", "number"),
    })
    agg = aggregate(wf, "Collect alerts")
    mail = email(wf, "Alert e-mail (Mailpit)", "={{ $('Config').first().json.report_to }}",
                 "=[Automation Lab] FX alert: {{ $json.data.map(r => 'USD/' + r.quote + ' ' + r.change_pct + '%').join(', ') }}",
                 html=ALERT_HTML).retry(3, 2000)
    note = postgres_insert(wf, "Record notification", "notifications", {
        "channel": "email", "target": "={{ $('Config').first().json.report_to }}",
        "subject": "={{ $('Alert e-mail (Mailpit)').item.json.accepted ? 'FX alert sent' : 'FX alert' }}",
        "body": "={{ JSON.stringify($('Collect alerts').first().json.data) }}",
        "severity": "warning", "sent_at": "={{ $now.toISO() }}",
    }).on_error("continueRegularOutput")
    quiet = noop(wf, "Within threshold")
    suppressed = noop(wf, "Suppressed (cooldown)")
    wf.chain(trg, cfg, req, fetch, ok)
    wf.connect(manual, cfg)
    wf.connect(ok, last, out=0)
    wf.connect(ok, fail, out=1)
    wf.chain(last, compare, store, moved)
    wf.connect(moved, cool, out=0)
    wf.connect(moved, quiet, out=1)
    wf.chain(cool, first)
    wf.connect(first, keep, out=0)
    wf.connect(first, suppressed, out=1)
    wf.chain(keep, agg, mail, note)
    wf.sticky(
        "## M05 - Exchange-rate watcher\n"
        "Provider = mock-api `/rates/latest` (USD base, rates drift a little on every call). Fetch via **P02** "
        "(backoff), compare each quote with its last `rate_history` row, store the new rates, alert when a pair "
        "moved >= `threshold_pct` (Config).\n\n"
        "Per-pair **cooldown** in Redis (6 h) keeps a volatile pair from spamming. First run: no previous -> no alert.",
        pos=(-40, -330), width=640, height=220)
    return wf


if __name__ == "__main__":
    build().save()
