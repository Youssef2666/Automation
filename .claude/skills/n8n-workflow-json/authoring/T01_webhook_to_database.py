#!/usr/bin/env python
"""T01 - Webhook to Database.

POST /webhook/t01-orders (header auth X-Lab-Key) -> validate schema -> 400 on bad payload
-> P03 idempotency guard -> 200 {duplicate:true} on replay -> insert webhook_events -> 201 {ok, id, external_id}.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, catalog_id, cond_bool, code, execute_workflow, if_, postgres_insert,  # noqa: E402
                         respond, set_fields, webhook)

VALIDATE_JS = r"""
// One item in (the webhook request). Validate the JSON body field by field and report every problem at once.
const body = $input.first().json.body || {};
const errors = [];
const isStr = (v) => typeof v === 'string' && v.trim().length > 0;
if (!isStr(body.external_id)) errors.push('external_id: non-empty string required');
if (!isStr(body.order_number)) errors.push('order_number: non-empty string required');
const email = body.customer && body.customer.email;
if (!isStr(email) || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) errors.push('customer.email: valid e-mail required');
if (!Array.isArray(body.items) || body.items.length === 0) errors.push('items: non-empty array required');
else body.items.forEach((it, i) => {
  if (!isStr(it.sku)) errors.push(`items[${i}].sku: required`);
  if (!(Number(it.qty) > 0)) errors.push(`items[${i}].qty: positive number required`);
});
if (typeof body.total !== 'number' || !(body.total >= 0)) errors.push('total: number >= 0 required');
if (body.ordered_at !== undefined && Number.isNaN(Date.parse(body.ordered_at))) errors.push('ordered_at: ISO-8601 date required');
return [{ json: {
  valid: errors.length === 0,
  errors,
  external_id: isStr(body.external_id) ? body.external_id.trim() : null,
  event_type: 'order.created',
  source: isStr(body.source) ? body.source : 'webhook',
}}];
"""


def build() -> Workflow:
    wf = Workflow("T01", "webhook-to-database", "Webhook to Database", tags=["Triggers"],
                  error_workflow=catalog_id("P01"),
                  description="Validated, idempotent webhook ingestion into Postgres with proper HTTP responses.")
    hook = webhook(wf, "Webhook", "t01-orders", auth="header")
    val = code(wf, "Validate payload", VALIDATE_JS)
    ok = if_(wf, "Valid?", [cond_bool("={{ $json.valid }}")])
    bad = respond(wf, "Respond 400", body="={{ { ok: false, errors: $json.errors } }}", code=400)
    guard_in = set_fields(wf, "Guard input", {
        "external_id": "={{ $json.external_id }}",
        "scope": "t01-orders",
        "ttl_seconds": 86400,
    })
    guard = execute_workflow(wf, "Idempotency guard (P03)", catalog_id("P03"), cached_name="P03 - Idempotency guard")
    dup = if_(wf, "Duplicate?", [cond_bool("={{ $json.duplicate }}")])
    dup_resp = respond(wf, "Respond 200 (duplicate)",
                       body="={{ { ok: true, duplicate: true, external_id: $json.external_id, seen: $json.count } }}")
    ins = postgres_insert(wf, "Insert webhook_events", "webhook_events", {
        "external_id": "={{ $('Validate payload').item.json.external_id }}",
        "source": "={{ $('Validate payload').item.json.source }}",
        "event_type": "={{ $('Validate payload').item.json.event_type }}",
        "payload": "={{ JSON.stringify($('Webhook').item.json.body) }}",
        "received_at": "={{ $now.toISO() }}",
    }, returning=True).retry(3, 1000)
    created = respond(wf, "Respond 201", body="={{ { ok: true, id: $json.id, external_id: $json.external_id } }}",
                      code=201)
    wf.chain(hook, val, ok)
    wf.connect(ok, guard_in, out=0)
    wf.connect(ok, bad, out=1)
    wf.chain(guard_in, guard, dup)
    wf.connect(dup, dup_resp, out=0)
    wf.connect(dup, ins, out=1)
    wf.chain(ins, created)
    wf.sticky(
        "## T01 - Webhook to Database\n"
        "`POST /webhook/t01-orders` with header `X-Lab-Key: <LAB_WEBHOOK_KEY>`.\n\n"
        "- bad schema -> **400** with every error listed\n"
        "- replayed `external_id` -> **200** `{duplicate:true}` (P03, Redis, 24 h)\n"
        "- new event -> row in `webhook_events` -> **201** with the row id\n\n"
        "Failures anywhere -> P01 error handler.",
        pos=(-40, -330), width=560, height=230)
    return wf


if __name__ == "__main__":
    build().save()
