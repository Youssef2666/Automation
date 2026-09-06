#!/usr/bin/env python
"""P02 - HTTP with backoff (sub-workflow).

Input (one item): {url, method?="GET", query?: object, body?: object, headers?: object,
                   max_attempts?=5, base_ms?=500, timeout_ms?=10000}
Output (one flat item): {ok, status, attempts, terminal, retryable, response, data}

Loop: Attempt state -> HTTP (never throws on 4xx/5xx, connection errors go to the error output) -> Classify
-> Retry? -> Wait(backoff) -> Attempt state ... until 2xx, a terminal error, or max_attempts.
Retryable: network error, 408, 425, 429, 5xx. Terminal: every other 4xx. Honors Retry-After.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, catalog_id, code, cond_bool, execute_workflow, http, if_, manual_trigger,  # noqa: E402
                         set_fields, sub_trigger, wait)

CLASSIFY_JS = r"""
// Runs once per attempt with the HTTP node's output (success output or error output) as $input.
const state = $('Attempt state').last().json;
const r = $input.first().json || {};
const failed = r.error !== undefined && r.statusCode === undefined;   // connection-level failure (error output)
const status = failed ? 0 : Number(r.statusCode || 0);
const body = failed ? null : r.body;
const headers = (r.headers || {});
const ok = status >= 200 && status < 300;
const retryable = !ok && (status === 0 || status === 408 || status === 425 || status === 429 || status >= 500);
const exhausted = state.attempt >= state.max_attempts;
// exponential backoff with cap and +0..25% jitter; Retry-After (seconds) wins when larger
let delay = Math.min(30000, state.base_ms * Math.pow(2, state.attempt - 1));
delay = Math.round(delay + Math.random() * delay * 0.25);
const retryAfter = Number(headers['retry-after']);
if (retryAfter > 0) delay = Math.max(delay, retryAfter * 1000);
const message = failed ? String((r.error && (r.error.message || r.error.description)) || 'request failed')
  : (typeof body === 'string' ? body : JSON.stringify(body));
return [{ json: {
  ...state,
  ok, status, retryable,
  retry: !ok && retryable && !exhausted,
  terminal: !ok && (!retryable || exhausted),
  delay_ms: delay,
  attempts: state.attempt,
  data: body,
  response: ok ? `HTTP ${status} after ${state.attempt} attempt(s)` : `HTTP ${status || 'ERR'}: ${String(message).slice(0, 300)}`,
}}];
"""


def build() -> Workflow:
    wf = Workflow("P02", "retry-backoff", "HTTP with backoff", tags=["pattern", "P02"],
                  error_workflow=catalog_id("P01"),
                  description="HTTP request with exponential backoff; classifies retryable vs terminal errors.")
    trg = sub_trigger(wf)
    state = set_fields(wf, "Attempt state", {
        "attempt": ("={{ (Number($json.attempt) || 0) + 1 }}", "number"),
        "url": "={{ $json.url }}",
        "method": "={{ String($json.method || 'GET').toUpperCase() }}",
        "query": ("={{ $json.query || {} }}", "object"),
        "body": ("={{ $json.body ?? null }}", "object"),
        "headers": ("={{ $json.headers || {} }}", "object"),
        "max_attempts": ("={{ Number($json.max_attempts) > 0 ? Number($json.max_attempts) : 5 }}", "number"),
        "base_ms": ("={{ Number($json.base_ms) > 0 ? Number($json.base_ms) : 500 }}", "number"),
        "timeout_ms": ("={{ Number($json.timeout_ms) > 0 ? Number($json.timeout_ms) : 10000 }}", "number"),
    })
    req = http(wf, "HTTP Request", "={{ $json.url }}", method="={{ $json.method }}", full_response=True,
               never_error=True, timeout_ms=10000, response="autodetect")
    req.parameters.update({
        "sendQuery": True, "specifyQuery": "json", "jsonQuery": "={{ JSON.stringify($json.query || {}) }}",
        "sendHeaders": True, "specifyHeaders": "json", "jsonHeaders": "={{ JSON.stringify($json.headers || {}) }}",
        "sendBody": True,  # boolean params ignore expressions (proven by P06); an empty {} body on GET is harmless
        "specifyBody": "json", "jsonBody": "={{ JSON.stringify($json.body ?? {}) }}",
    })
    req.parameters["options"]["timeout"] = "={{ $json.timeout_ms }}"
    req.on_error("continueErrorOutput")
    classify = code(wf, "Classify", CLASSIFY_JS)
    retry = if_(wf, "Retry?", [cond_bool("={{ $json.retry }}")])
    backoff = wait(wf, "Backoff", amount="={{ $json.delay_ms / 1000 }}", unit="seconds")
    # A Code node, not a Set node: Set's typed "object" field rejects arrays (JSON bodies are often arrays).
    result = code(wf, "Result", """
const r = $input.first().json;
return [{ json: { ok: r.ok, status: r.status, attempts: r.attempts, terminal: r.terminal, retryable: r.retryable,
                  response: r.response, data: r.data === undefined ? null : r.data, url: r.url } }];
""")
    wf.chain(trg, state, req)
    wf.connect(req, classify, out=0)
    wf.connect(req, classify, out=1)
    wf.chain(classify, retry)
    wf.connect(retry, backoff, out=0)
    wf.connect(retry, result, out=1)
    wf.connect(backoff, state)
    backoff.at(520, 260)
    wf.sticky(
        "## P02 - HTTP with backoff\n"
        "Input `{url, method?, query?, body?, headers?, max_attempts?=5, base_ms?=500}`.\n\n"
        "- 2xx -> done\n- network error / 408 / 425 / 429 / 5xx -> **retry** after `base_ms * 2^(n-1)` (+jitter, "
        "cap 30 s, honours `Retry-After`)\n- other 4xx -> **terminal**, no retry\n\n"
        "Returns `{ok, status, attempts, terminal, retryable, response, data}`; never throws - the caller decides.",
        pos=(-40, -330), width=620, height=250)
    return wf


def build_harness() -> Workflow:
    """test/harness.json - calls P02 once per case against the mock API's failure modes."""
    wf = Workflow("P02", "harness", "P02 harness (test)", tags=["test"], error_workflow=catalog_id("P01"))
    t = manual_trigger(wf, "Start")
    cases = code(wf, "Cases", """
return [
  { json: { name: 'flaky: 503 then 200',      url: 'http://mock-api:8080/flaky',       max_attempts: 4, base_ms: 200 } },
  { json: { name: 'down: 503 x3 -> exhausted', url: 'http://mock-api:8080/health/down', max_attempts: 3, base_ms: 200 } },
  { json: { name: 'missing: 404 terminal',    url: 'http://mock-api:8080/no-such-route', max_attempts: 4, base_ms: 200 } },
  { json: { name: 'refused: connection error', url: 'http://mock-api:1/health',         max_attempts: 2, base_ms: 200, timeout_ms: 2000 } },
];
""")
    call = execute_workflow(wf, "HTTP with backoff (P02)", catalog_id("P02"), mode="each",
                            cached_name="P02 - HTTP with backoff")
    report = code(wf, "Report", """
const cases = $('Cases').all().map(i => i.json.name);
return $input.all().map((it, i) => ({ json: { case: cases[i], ok: it.json.ok, status: it.json.status,
  attempts: it.json.attempts, terminal: it.json.terminal, retryable: it.json.retryable, response: it.json.response } }));
""")
    wf.chain(t, cases, call, report)
    return wf


if __name__ == "__main__":
    main = build()
    main.save()
    build_harness().save(main.folder() / "test" / "harness.json")
