#!/usr/bin/env python
"""P06 - Replay test payloads (harness workflow) + test/cases.json (single source for the host-side replay.py).

Manual Trigger -> Test plan (cases) -> HTTP Request (never throws on non-2xx) -> Assert (per case)
-> Aggregate -> Report -> Stop and Error when anything failed (so a scheduled run alerts through P01).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (REPO, Workflow, aggregate, catalog_id, code, cond_num, http, if_, manual_trigger,  # noqa: E402
                         noop, stop_error)

ORDER = json.loads((REPO / "seed" / "payloads" / "t01-order.json").read_text(encoding="utf-8"))
ORDER["external_id"] = "evt-p06-replay"          # fixed id: first run 201, later runs 200 (duplicate) - both pass
INVALID = json.loads((REPO / "seed" / "payloads" / "t01-invalid.json").read_text(encoding="utf-8"))

# In-network hostnames. test/replay.py maps them to localhost ports when run from the host.
CASES = [
    {"name": "mock-api health", "method": "GET", "url": "http://mock-api:8080/health",
     "expect_status": 200, "expect_path": "status", "expect_value": "ok"},
    {"name": "mock-api simulated outage", "method": "GET", "url": "http://mock-api:8080/health/down",
     "expect_status": 503, "expect_path": "status", "expect_value": "down"},
    {"name": "P01 fixture webhook accepts", "method": "POST", "url": "http://n8n:5678/webhook/p01-fail",
     "body": {"source": "p06"}, "expect_status": 200, "expect_path": "message", "expect_value": "Workflow was started"},
    {"name": "T01 valid order (201 first, 200 replay)", "method": "POST", "url": "http://n8n:5678/webhook/t01-orders",
     "headers": {"X-Lab-Key": "lab-demo-key", "Content-Type": "application/json"}, "body": ORDER,
     "expect_status": [200, 201], "expect_path": "ok", "expect_value": True, "optional": True},
    {"name": "T01 invalid order -> 400 with errors", "method": "POST", "url": "http://n8n:5678/webhook/t01-orders",
     "headers": {"X-Lab-Key": "lab-demo-key", "Content-Type": "application/json"}, "body": INVALID,
     "expect_status": 400, "expect_path": "ok", "expect_value": False, "optional": True},
    {"name": "T01 without key -> 403", "method": "POST", "url": "http://n8n:5678/webhook/t01-orders",
     "headers": {"Content-Type": "application/json"}, "body": ORDER, "expect_status": 403, "optional": True},
]

ASSERT_JS = r"""
// Runs once per case (paired with its Test plan item). Works for both HTTP outputs (response / error output).
const c = $('Test plan').item.json;
const r = $json;
const failed = r.error !== undefined && r.statusCode === undefined;
const status = failed ? 0 : Number(r.statusCode || 0);
const body = failed ? null : r.body;
const expected = Array.isArray(c.expect_status) ? c.expect_status : [c.expect_status];
const problems = [];
let skipped = false;
if (c.optional && (status === 404 || status === 0)) {
  skipped = true;                                     // target workflow not imported / not active
} else {
  if (!expected.includes(status)) problems.push(`status ${status}, expected ${expected.join(' or ')}`);
  if (c.expect_path !== undefined) {
    const got = c.expect_path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), body);
    if (JSON.stringify(got) !== JSON.stringify(c.expect_value)) problems.push(`${c.expect_path} = ${JSON.stringify(got)}, expected ${JSON.stringify(c.expect_value)}`);
  }
}
return { json: { name: c.name, url: c.url, pass: !skipped && problems.length === 0, skipped, status,
                 detail: skipped ? 'skipped (target not available)' : (problems.join('; ') || 'ok'),
                 error: failed ? String((r.error && r.error.message) || 'request failed') : null } };
"""

REPORT_JS = r"""
const cases = $input.first().json.data || [];
const passed = cases.filter(c => c.pass).length;
const skipped = cases.filter(c => c.skipped).length;
const failed = cases.filter(c => !c.pass && !c.skipped);
const lines = cases.map(c => `${c.pass ? 'PASS' : c.skipped ? 'SKIP' : 'FAIL'}  ${c.name}  [${c.status}] ${c.detail}`);
return [{ json: { total: cases.length, passed, failed: failed.length, skipped,
                  failed_names: failed.map(c => c.name), report: lines.join('\n'), cases } }];
"""


def build() -> Workflow:
    wf = Workflow("P06", "testing", "Replay test payloads", tags=["pattern", "P06"],
                  error_workflow=catalog_id("P01"),
                  description="Replays test/ payloads against webhooks and services and asserts status + JSON.")
    trg = manual_trigger(wf, "Run once (manual / CLI)")
    plan = code(wf, "Test plan", "// Same cases as test/cases.json (embedded by the authoring script).\n"
                                 "const cases = " + json.dumps(CASES, indent=2) + ";\n"
                                 "return cases.map(c => ({ json: c }));")
    req = http(wf, "HTTP Request", "={{ $json.url }}", method="={{ $json.method }}", full_response=True,
               never_error=True, timeout_ms=15000, response="autodetect")
    req.parameters.update({
        "sendHeaders": True, "specifyHeaders": "json", "jsonHeaders": "={{ JSON.stringify($json.headers || {}) }}",
        "sendBody": True,  # boolean params do not take expressions reliably; an empty {} body on GET is harmless
        "specifyBody": "json", "jsonBody": "={{ JSON.stringify($json.body ?? {}) }}",
    })
    req.on_error("continueErrorOutput")
    assert_ = code(wf, "Assert", ASSERT_JS, per_item=True)
    agg = aggregate(wf, "Collect results")
    report = code(wf, "Report", REPORT_JS)
    any_failed = if_(wf, "Any failures?", [cond_num("={{ $json.failed }}", "gt", 0)])
    fail = stop_error(wf, "Fail the run", "=P06: {{ $json.failed }} of {{ $json.total }} case(s) failed: {{ $json.failed_names.join(', ') }}\n{{ $json.report }}")
    green = noop(wf, "All green")
    wf.chain(trg, plan, req)
    wf.connect(req, assert_, out=0)
    wf.connect(req, assert_, out=1)
    wf.chain(assert_, agg, report, any_failed)
    wf.connect(any_failed, fail, out=0)
    wf.connect(any_failed, green, out=1)
    wf.sticky(
        "## P06 - Replay test payloads\n"
        "Cases = `test/cases.json` (embedded here by the authoring script). Each case: method, url, headers, body, "
        "expected status (or list), optional JSON path + value. `optional: true` cases are *skipped* (not failed) "
        "when the target answers 404 / is unreachable.\n\n"
        "Non-2xx responses are data, not errors (full response + never error), so one failing case never hides "
        "the others. Any failure ends in Stop and Error -> P01 alerts if this runs on a schedule.\n\n"
        "Hosts: `n8n:5678` / `mock-api:8080` are compose service names (this is the one place the lab targets n8n itself).",
        pos=(-40, -360), width=680, height=280)
    return wf


if __name__ == "__main__":
    wf = build()
    wf.save()
    out = wf.folder() / "test" / "cases.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(CASES, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out.relative_to(REPO)} ({len(CASES)} cases)")
