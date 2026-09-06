#!/usr/bin/env python
"""P04 - Rate limit gate (sub-workflow) + test harness.

Input (one item): {key: string, limit?: number = 5, window_seconds?: number = 10}
Output (one flat item): {ok, allowed, count, limit, remaining, retry_after_ms, window_key, window_seconds, key, response}

Fixed-window counter in Redis: INCR `rl:<key>:<floor(now / window)>` with TTL = window + 1 s. `allowed` is true while
the count is within the limit; otherwise `retry_after_ms` says how long until the next window opens. Never throws
(only a missing `key` raises Stop and Error so the caller's P01 sees a clear message).

Caller wiring: gate -> If allowed? -> (true) do the call; (false) Wait retry_after_ms -> gate again.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, catalog_id, code, cond_bool, cond_not_empty, execute_workflow, http, if_,  # noqa: E402
                         loop, manual_trigger, redis, stop_error, sub_trigger, wait)

NORMALIZE_JS = r"""
// One call = one item. Defaults: 5 requests per 10 s window. The window index is aligned to the epoch, so every
// worker that shares the key also shares the same window boundaries.
const j = $json;
const key = String(j.key).trim();
const limit = Number(j.limit) > 0 ? Math.floor(Number(j.limit)) : 5;
const window_seconds = Number(j.window_seconds) > 0 ? Math.floor(Number(j.window_seconds)) : 10;
const now = Date.now();
const window_index = Math.floor(now / 1000 / window_seconds);
return { key, limit, window_seconds, window_index,
         window_key: `rl:${key}:${window_index}`,
         window_end_ms: (window_index + 1) * window_seconds * 1000,
         ttl_seconds: window_seconds + 1 };
"""

RESULT_JS = r"""
// Redis INCR outputs { "<window_key>": <count> }; the normalized input is one item, so .first() is safe.
const s = $('Normalize input').first().json;
const count = Number(Object.values($input.first().json)[0]);
const allowed = count <= s.limit;
const remaining = Math.max(0, s.limit - count);
const retry_after_ms = allowed ? 0 : Math.max(0, s.window_end_ms - Date.now());
return [{ json: {
  ok: true, allowed, count, limit: s.limit, remaining, retry_after_ms,
  window_key: s.window_key, window_seconds: s.window_seconds, key: s.key,
  response: allowed
    ? `allowed (${count}/${s.limit} in window, ${remaining} left)`
    : `throttled (${count}/${s.limit}); next window in ${retry_after_ms} ms`,
}}];
"""


def build() -> Workflow:
    wf = Workflow("P04", "rate-limiting", "Rate limit gate", tags=["pattern", "P04"],
                  error_workflow=catalog_id("P01"), caller_policy="workflowsFromSameOwner",
                  description="Shared fixed-window rate limiter (Redis INCR per key and window); tells the caller "
                              "whether to go now or how long to wait.")
    trg = sub_trigger(wf)
    has_key = if_(wf, "Has key?", [cond_not_empty("={{ String($json.key ?? '') }}")])
    bad = stop_error(wf, "Missing key", "P04 rate limit gate: input item needs a non-empty key")
    norm = code(wf, "Normalize input", NORMALIZE_JS, per_item=True)
    count = redis(wf, "Count in window (INCR + TTL)", "incr", "={{ $json.window_key }}",
                  ttl="={{ $json.ttl_seconds }}").retry(3, 500)
    result = code(wf, "Result", RESULT_JS)
    wf.chain(trg, has_key)
    wf.connect(has_key, norm, out=0)
    wf.connect(has_key, bad, out=1)
    wf.chain(norm, count, result)
    wf.sticky(
        "## P04 - Rate limit gate\n"
        "Call with `{key, limit?=5, window_seconds?=10}` **before** each outbound request.\n\n"
        "`INCR rl:<key>:<floor(now/window)>` (TTL window+1 s): count <= limit -> `allowed: true`; otherwise "
        "`retry_after_ms` = time until the next window. Returns `{ok, allowed, count, limit, remaining, "
        "retry_after_ms, window_key, response}` and never throws.\n\n"
        "Caller: If allowed -> request; else Wait `retry_after_ms` -> ask the gate again. Every worker that shares "
        "the key shares the budget.",
        pos=(-40, -330), width=640, height=260)
    return wf


def build_harness() -> Workflow:
    """test/harness.json - 8 requests to /ratelimited without the gate (expect 3 x 429), then 8 through it (expect none)."""
    wf = Workflow("P04", "harness", "P04 harness (test)", tags=["test"], error_workflow=catalog_id("P01"))
    t = manual_trigger(wf, "Start")
    burst = code(wf, "Burst of 8 (no gate)", """
return Array.from({ length: 8 }, (_, i) => ({ json: { n: i + 1 } }));
""")
    hit_raw = http(wf, "GET /ratelimited (no gate)", "http://mock-api:8080/ratelimited", full_response=True,
                   never_error=True, timeout_ms=5000)
    count_raw = code(wf, "Count 429s (no gate)", """
const rs = $input.all().map(i => i.json);
return [{ json: {
  ungated_requests: rs.length,
  ungated_ok: rs.filter(r => r.statusCode === 200).length,
  ungated_429: rs.filter(r => r.statusCode === 429).length,
}}];
""")
    # The server's 10 s window slides from the first request it stored; the burst above filled it, so let it drain.
    settle = wait(wf, "Let the server bucket drain", amount=11, unit="seconds")
    # limit 2 per 10 s, not 5: the gate's window is fixed while the server's slides, and any sliding 10 s interval
    # touches at most two fixed windows -> at most 4 requests, always under the server's 5 (README, Trade-offs).
    gated = code(wf, "8 gated requests", """
return Array.from({ length: 8 }, (_, i) => ({ json: { n: i + 1, key: 'harness', limit: 2, window_seconds: 10 } }));
""")
    lp = loop(wf, "One at a time", batch_size=1)
    gate = execute_workflow(wf, "Rate limit gate (P04)", catalog_id("P04"), cached_name="P04 - Rate limit gate")
    allowed = if_(wf, "Allowed?", [cond_bool("={{ $json.allowed }}")])
    hold = wait(wf, "Wait for next window", amount="={{ ($json.retry_after_ms + 250) / 1000 }}", unit="seconds")
    hit = http(wf, "GET /ratelimited (gated)", "http://mock-api:8080/ratelimited", full_response=True,
               never_error=True, timeout_ms=5000)
    report = code(wf, "Report", """
const gated = $input.all().map(i => i.json);
const raw = $('Count 429s (no gate)').first().json;
const gated_429 = gated.filter(r => r.statusCode === 429).length;
// $('Node').all() after a loop only returns the latest run; walk the run indexes to count every throttle.
let throttled = 0;
for (let r = 0; r < 100; r++) {
  let items;
  try { items = $('Rate limit gate (P04)').all(0, r); } catch (e) { break; }
  if (!items || !items.length) break;
  throttled += items.filter(i => i.json.allowed === false).length;
}
return [{ json: {
  ungated: { requests: raw.ungated_requests, ok: raw.ungated_ok, status_429: raw.ungated_429 },
  gated: { requests: gated.length, ok: gated.filter(r => r.statusCode === 200).length, status_429: gated_429,
           throttled_by_gate: throttled },
  pass: raw.ungated_429 > 0 && gated_429 === 0,
  response: `without gate: ${raw.ungated_429}/${raw.ungated_requests} got 429; with gate: ${gated_429}/${gated.length} got 429`,
}}];
""")
    wf.chain(t, burst, hit_raw, count_raw, settle, gated, lp)
    wf.connect(lp, gate, out=1)
    wf.chain(gate, allowed)
    wf.connect(allowed, hit, out=0)
    wf.connect(allowed, hold, out=1)
    wf.connect(hold, gate)          # ask again after the wait (the gate echoes key/limit/window_seconds)
    wf.connect(hit, lp)             # back into the loop
    wf.connect(lp, report, out=0)   # done
    hold.at(2340, 240)
    wf.sticky(
        "## P04 harness\n"
        "Phase 1: 8 parallel requests to `/ratelimited` (5 per 10 s per client) -> expect 3 x 429.\n"
        "Phase 2: same 8 requests one at a time through the gate (`key=harness`, **2 per 10 s** - half the server "
        "quota because the server's window slides); every 3rd request is throttled, waits for the next window, "
        "asks again -> expect 0 x 429 and 3 throttles.",
        pos=(-40, -260), width=620, height=190)
    return wf


if __name__ == "__main__":
    main = build()
    main.save()
    build_harness().save(main.folder() / "test" / "harness.json")
