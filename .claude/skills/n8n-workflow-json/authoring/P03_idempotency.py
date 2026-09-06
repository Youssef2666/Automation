#!/usr/bin/env python
"""P03 - Idempotency guard (sub-workflow).

Input (one item): {external_id: string, scope?: string = "default", ttl_seconds?: number = 86400}
Output (one flat item): {ok, duplicate, count, key, external_id, scope, response}

Redis INCR on `idem:<scope>:<external_id>` with a TTL. count == 1 -> first time; count > 1 -> duplicate.
Callers: execute_workflow(wf, "Idempotency guard (P03)", catalog_id("P03")) after a Set node that builds the input.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, catalog_id, cond_not_empty, if_, redis, set_fields, stop_error,  # noqa: E402
                         sub_trigger)


def build() -> Workflow:
    wf = Workflow("P03", "idempotency", "Idempotency guard", tags=["pattern", "P03"],
                  error_workflow=catalog_id("P01"), caller_policy="workflowsFromSameOwner",
                  description="Marks an external id as seen (Redis INCR + TTL) and tells the caller whether it is a replay.")
    trg = sub_trigger(wf)
    has_id = if_(wf, "Has external_id?", [cond_not_empty("={{ String($json.external_id ?? '') }}")])
    bad = stop_error(wf, "Missing external_id", "P03 idempotency guard: input item needs a non-empty external_id")
    norm = set_fields(wf, "Normalize input", {
        "external_id": "={{ String($json.external_id).trim() }}",
        "scope": "={{ String($json.scope || 'default').trim() }}",
        "ttl_seconds": ("={{ Number($json.ttl_seconds) > 0 ? Math.floor(Number($json.ttl_seconds)) : 86400 }}", "number"),
    })
    mark = redis(wf, "Mark seen (INCR + TTL)", "incr", "=idem:{{ $json.scope }}:{{ $json.external_id }}",
                 ttl="={{ $json.ttl_seconds }}").retry(3, 500)
    out = set_fields(wf, "Result", {
        "ok": True,
        "duplicate": ("={{ Number(Object.values($json)[0]) > 1 }}", "boolean"),
        "count": ("={{ Number(Object.values($json)[0]) }}", "number"),
        "key": "={{ Object.keys($json)[0] }}",
        "external_id": "={{ $('Normalize input').item.json.external_id }}",
        "scope": "={{ $('Normalize input').item.json.scope }}",
        "ttl_seconds": ("={{ $('Normalize input').item.json.ttl_seconds }}", "number"),
        "response": "={{ Number(Object.values($json)[0]) > 1 ? 'duplicate (seen ' + Object.values($json)[0] + ' times)' : 'first time' }}",
    })
    wf.chain(trg, has_id)
    wf.connect(has_id, norm, out=0)
    wf.connect(has_id, bad, out=1)
    wf.chain(norm, mark, out)
    wf.sticky(
        "## P03 - Idempotency guard\n"
        "Call with `{external_id, scope?, ttl_seconds?}` **before** the side effect.\n\n"
        "`INCR idem:<scope>:<external_id>` (TTL default 24 h): count 1 = first time, >1 = replay.\n"
        "Returns `{ok, duplicate, count, key, response}` - the caller skips the insert/charge when `duplicate` is true.\n\n"
        "Trade-off: the key is marked even if the caller's side effect fails afterwards (see README for the "
        "mark-after-success variant).",
        pos=(-40, -330), width=600, height=250)
    return wf


if __name__ == "__main__":
    build().save()
