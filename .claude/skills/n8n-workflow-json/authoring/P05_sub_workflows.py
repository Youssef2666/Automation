#!/usr/bin/env python
"""P05 - Lookup customer (reference sub-workflow for the sub-workflow contract) + test/harness.json.

Input (one item): {email?: string, external_id?: string}   - at least one of the two
Output (one flat item): {ok, found, customer_id, external_id, name, email, phone, company, country, city, segment,
                         lookup_by, lookup_value, response}

The block itself is small on purpose: it exists to show the contract every shared building block in this repo
follows (P02, P03, P04, P07, P08 do the same):

  1. Execute Workflow Trigger v1.1, `inputSource: passthrough` - the caller's item arrives unchanged.
  2. Validate first; missing input -> Stop and Error with a message that names the block and the field.
  3. Normalize (defaults, trimming, casing) in one node so every later node sees one shape.
  4. Do the one documented thing; service calls carry `.retry` when they are safe to repeat (a SELECT is).
  5. Result: exactly one flat item, `ok` boolean + `response` string, plus the useful fields at top level.
     "Not found" is an answer, not an error: `ok: true, found: false` - the caller decides what that means.

Callers: set_fields(...) that builds {email} or {external_id} -> execute_workflow(wf, "Lookup customer (P05)",
catalog_id("P05")); see the README for mode each/once and the error lane. Live check: test/harness.json.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, catalog_id, code, cond_not_empty, execute_workflow, if_, manual_trigger,  # noqa: E402
                         noop, postgres_query, stop_error, sub_trigger, wf_id)

P05_ID = catalog_id("P05")

NORMALIZE_JS = r"""
// One call = one item: {email?, external_id?}. Both keys are normalised to the form stored in `customers`
// (e-mail lower-cased, external id upper-cased) or become null, and `lookup_by` records which one wins.
const j = $json;
const email = j.email === undefined || j.email === null || String(j.email).trim() === ''
  ? null : String(j.email).trim().toLowerCase();
const external_id = j.external_id === undefined || j.external_id === null || String(j.external_id).trim() === ''
  ? null : String(j.external_id).trim().toUpperCase();
const lookup_by = external_id ? 'external_id' : 'email';
return { json: { email, external_id, lookup_by, lookup_value: external_id || email } };
"""

FIND_SQL = """select id, external_id, name, email, phone, company, country, city, segment, created_at
  from customers
 where ($1::text is not null and external_id = $1)
    or ($1::text is null and lower(email) = $2)
 order by id
 limit 1"""

FIND_PARAMS = "={{ [ $json.external_id, $json.email ] }}"

RESULT_JS = r"""
// The contract's last node: exactly one flat item, always with `ok` and `response`.
// "Always output data" on the SELECT gives one empty item when no row matched, so this node runs either way.
const input = $('Normalize input').first().json;
const rows = $input.all().map((i) => i.json).filter((r) => r && r.id !== undefined);
const row = rows[0] || null;
const found = row !== null;
const who = found ? `${row.external_id} ${row.name} (${row.company || 'no company'}, ${row.segment})` : '';
return [{ json: {
  ok: true,
  found,
  customer_id: found ? row.external_id : null,
  external_id: found ? row.external_id : input.external_id,
  name: found ? row.name : null,
  email: found ? row.email : input.email,
  phone: found ? row.phone : null,
  company: found ? row.company : null,
  country: found ? row.country : null,
  city: found ? row.city : null,
  segment: found ? row.segment : null,
  lookup_by: input.lookup_by,
  lookup_value: input.lookup_value,
  response: found
    ? `found ${who} by ${input.lookup_by}`
    : `no customer with ${input.lookup_by} ${input.lookup_value}`,
}}];
"""


def build() -> Workflow:
    wf = Workflow("P05", "sub-workflows", "Lookup customer", tags=["pattern", "P05"],
                  error_workflow=catalog_id("P01"), caller_policy="workflowsFromSameOwner",
                  description="Reference building block for the sub-workflow contract: {email | external_id} in, "
                              "one flat {ok, found, ..., response} item out; bad input -> Stop and Error.")
    trg = sub_trigger(wf)
    valid = if_(wf, "Has email or external_id?", [
        cond_not_empty("={{ String($json.email ?? '') }}"),
        cond_not_empty("={{ String($json.external_id ?? '') }}"),
    ], combinator="or")
    bad = stop_error(wf, "Missing lookup key", "P05 lookup customer: input item needs email or external_id")
    norm = code(wf, "Normalize input", NORMALIZE_JS, per_item=True)
    find = postgres_query(wf, "Find customer", FIND_SQL, params=FIND_PARAMS).retry(3, 1000).always_output()
    find.note("SELECT is safe to retry. Always output data: a miss is one empty item, so Result still runs.")
    result = code(wf, "Result", RESULT_JS)
    wf.chain(trg, valid)
    wf.connect(valid, norm, out=0)
    wf.connect(valid, bad, out=1)
    wf.chain(norm, find, result)
    wf.sticky(
        "## P05 - Lookup customer (sub-workflow contract)\n"
        "Call with `{email}` or `{external_id}` -> `{ok, found, customer_id, name, email, company, segment, ..., "
        "response}`.\n\n"
        "The contract every shared block follows: **passthrough trigger** -> **validate** (Stop and Error names "
        "the block and the field) -> **normalize** -> **one documented job** (`.retry` where safe) -> **Result**: "
        "exactly one flat item with `ok` + `response`.\n\n"
        "Not found is an answer (`ok: true, found: false`), not an error. Breaking changes get a new folder and id "
        "(`P05b-...`), never a silent edit - callers pin `catalog_id(\"P05\")`.",
        pos=(-40, -360), width=660, height=290)
    return wf


def build_harness() -> Workflow:
    """test/harness.json - a manual caller that exercises both lanes with the two call shapes that are reliable.

    Live finding (n8n 2.37.10, Execute Workflow v1.1): `mode: each` + `onError: continueErrorOutput` is only
    dependable when the call carries **one** item. A batch that contains a failing item is unreliable in three
    different ways - the error surfaces on a shifting branch index, or is dropped silently while the node reports
    success, or the node crashes (`assignPairedItems`) and echoes the raw input items on branch 0. A batch where
    nothing fails is fine. So the harness uses two calls:

      * three valid cases in one `each` call with **no** error lane - nothing there can fail the item-level check,
        and a batch call is allowed to fail the whole run;
      * the bad-input case in its own single-item `each` call **with** the error lane; for that shape the error
        item lands on branch 1, where the builder wires it.
    """
    wf = Workflow("P05", "harness", "P05 harness (test)", tags=["test"], error_workflow=catalog_id("P01"),
                  description="Calls P05 - Lookup customer twice: three valid cases in one 'each' call (no error "
                              "lane) and one bad-input item in its own call with the error lane. Test fixture only.")
    trg = manual_trigger(wf, "Run once (manual / CLI)")
    cases = code(wf, "Valid cases", r"""
return [
  { json: { case: 'hit by email',       email: 'selin.berg@lab.local' } },
  { json: { case: 'hit by external_id', external_id: ' cust-0002 ' } },
  { json: { case: 'miss',               email: 'nobody@lab.local' } },
];
""")
    call_ok = execute_workflow(wf, "Lookup customers (P05)", P05_ID, mode="each",
                              cached_name="P05 - Lookup customer")
    call_ok.note("mode: each, wait on, NO error lane: 3 items that cannot fail; a batch call fails the whole run")
    bad = code(wf, "Bad case", r"""
return [{ json: { case: 'bad input', note: 'neither email nor external_id -> Stop and Error in P05' } }];
""")
    call_bad = execute_workflow(wf, "Lookup bad input (P05)", P05_ID, mode="each",
                                cached_name="P05 - Lookup customer").on_error("continueErrorOutput")
    call_bad.note("one item + error lane: the only call shape whose error lane is dependable in 2.37")
    err_lane = noop(wf, "Error lane (output index 1)")
    report = code(wf, "Report", r"""
// Two calls, two questions:
//   1. the batch call answers one flat item per valid case (2 hits + 1 miss, all ok: true);
//   2. the single-item call with the error lane puts the Stop and Error on branch 1 and nothing on branch 0.
// Branch 1 is asserted for THIS call shape (one item per call). In a multi-item call the branch index moves with
// the batch, or the failing item is dropped, or the node crashes - see the README table.
const results = $('Lookup customers (P05)').all().map((i) => i.json);
const lane = (idx) => {
  try { return $('Lookup bad input (P05)').all(idx).map((i) => i.json); } catch (e) { return []; }
};
const errors = lane(1);            // error lane, where the builder wires it
const badResultLane = lane(0);     // must stay empty: a refused item is not a result
const e = errors[0] || {};
const message = typeof e.error === 'string' ? e.error : (e.error && e.error.message) || JSON.stringify(e);
const at = (value) => results.find((r) => r.lookup_value === value) || {};
const email = at('selin.berg@lab.local');
const byId = at('CUST-0002');
const miss = at('nobody@lab.local');
const checks = {
  three_results: results.length === 3,
  hit_by_email: email.found === true && email.customer_id === 'CUST-0001' && email.lookup_by === 'email',
  hit_by_external_id: byId.found === true && byId.customer_id === 'CUST-0002' && byId.lookup_by === 'external_id',
  miss_is_data: miss.ok === true && miss.found === false && miss.customer_id === null,
  one_error_on_index_1: errors.length === 1 && badResultLane.length === 0,
  error_names_the_block: message.includes('P05 lookup customer'),
};
const failed = Object.keys(checks).filter((k) => !checks[k]);
const pass = failed.length === 0;
const row = (c, r) => ({ case: c, lane: 'result (index 0)', ok: r.ok, found: r.found, customer_id: r.customer_id,
                         name: r.name, lookup_by: r.lookup_by, response: r.response });
return [{ json: {
  pass,
  checks,
  cases: [
    row('hit by email', email),
    row('hit by external_id', byId),
    row('miss', miss),
    { case: 'bad input', lane: 'error (index 1)', message },
  ],
  result_items: results.length,
  error_items: errors.length,
  response: pass
    ? "4 cases passed: 3 results from one 'each' call + 1 error item on output index 1 from the single-item call"
    : `FAILED: ${failed.join(', ')} (results=${results.length}, errors=${errors.length}, index0=${badResultLane.length})`,
}}];
""")
    wf.chain(trg, cases, call_ok, bad, call_bad)
    wf.connect(call_bad, report, out=0)      # a result here would mean P05 accepted an item without a lookup key
    wf.connect(call_bad, err_lane, out=1)
    wf.chain(err_lane, report)
    err_lane.at(1300, 220)
    report.at(1560, 0)
    wf.sticky(
        "## P05 harness\n"
        "Two calls, because in n8n 2.37 `mode: each` + error lane is only dependable **one item at a time**:\n"
        "**Lookup customers (P05)** takes the three valid cases (hit by e-mail, hit by external id, miss) in one "
        "batch call with no error lane - a batch is fine while nothing fails - and **Lookup bad input (P05)** "
        "sends the single item that has no lookup key with the error lane kept; for that shape the Stop and Error "
        "arrives on branch **1**. Report asserts all six checks. Put the same failure in a batch and it lands on "
        "a shifting branch, vanishes silently, or crashes the node.",
        pos=(-40, -300), width=760, height=220)
    return wf


if __name__ == "__main__":
    sub = build()
    sub.save()
    build_harness().save(sub.folder() / "test" / "harness.json")
    print("P05 id:", P05_ID, "harness id:", wf_id("P05", "harness"))
