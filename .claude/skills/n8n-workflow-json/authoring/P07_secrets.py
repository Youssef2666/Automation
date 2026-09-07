#!/usr/bin/env python
"""P07 - Signed request (credential-backed secret) - sub-workflow.

Input (one item): {url: string, method?: "GET"|"POST"|"PUT"|"PATCH"|"DELETE" = "GET", body?: object = {}}
Output (one flat item): {ok, status, host, method, url, credential, body, response}

The shared key (LAB_WEBHOOK_KEY in .env, dummy value in .env.example) never appears in this file, in the
workflow JSON, or in execution data: the HTTP Request node carries the "Webhook - header auth" credential
(CREDS["header"] -> {id, name} only) and n8n adds the X-Lab-Key header at run time. The workflow decides *where*
the key may travel (allowlist of hosts in a Set node), the credential decides *what* the key is.

Callers: execute_workflow(wf, "Signed request (P07)", catalog_id("P07")) after a Set node that builds
{url, method, body}. Live check: python patterns/P07-secrets/test/run.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, catalog_id, code, cond_bool, http, if_, set_fields, stop_error,  # noqa: E402
                         sub_trigger)

# Non-secret knob, visible on the canvas (ADR 0006): the only hosts the shared key may be sent to.
ALLOWED_HOSTS = ["mock-api:8080", "n8n:5678"]

VALIDATE_JS = r"""
// One item per call: {url, method?, body?}. The secret is NOT here - the HTTP Request node attaches it from the
// "Webhook - header auth" credential. This node only decides whether the target may receive that key at all.
const item = $input.first().json;
const allowed = (Array.isArray(item.allowed_hosts) ? item.allowed_hosts : []).map((h) => String(h).toLowerCase());
const url = String(item.url || '').trim();
const method = String(item.method || 'GET').trim().toUpperCase();
const body = item.body && typeof item.body === 'object' ? item.body : {};
let host = '';
let reason = '';
const m = url.match(/^(https?):\/\/([^/?#]+)/i);
if (!url) reason = 'input item needs a url';
else if (!m) reason = 'url must start with http:// or https:// (got "' + url.slice(0, 60) + '")';
else {
  host = m[2].toLowerCase();
  if (!allowed.includes(host)) {
    reason = 'host ' + host + ' is not in allowed_hosts - the shared key only travels to hosts named in the "Allowed hosts" node';
  }
}
if (!reason && !['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) reason = 'unsupported method ' + method;
return [{ json: { url, method, body, host, allowed: !reason, reason } }];
"""


def build() -> Workflow:
    wf = Workflow("P07", "secrets", "Signed request (credential-backed)", tags=["pattern", "P07"],
                  error_workflow=catalog_id("P01"), caller_policy="workflowsFromSameOwner",
                  description="Sends {url, method, body} with the shared X-Lab-Key taken from the 'Webhook - header auth' "
                              "credential; the key never appears in the workflow, only the allowlist of hosts does.")
    trg = sub_trigger(wf)
    cfg = set_fields(wf, "Allowed hosts", {"allowed_hosts": ALLOWED_HOSTS}, include_other=True)
    check = code(wf, "Validate target", VALIDATE_JS)
    gate = if_(wf, "Target allowed?", [cond_bool("={{ $json.allowed }}")])
    bad = stop_error(wf, "Refuse (key would leak)", "=P07 signed request: {{ $json.reason }}")
    call = http(wf, "Signed request (header credential)", "={{ $json.url }}", method="={{ $json.method }}",
                json_body="={{ JSON.stringify($json.body) }}", full_response=True, never_error=True,
                timeout_ms=15000, auth_cred="header", follow_redirects=False).on_error("continueErrorOutput")
    out = set_fields(wf, "Result", {
        "ok": ("={{ Number($json.statusCode) >= 200 && Number($json.statusCode) < 300 }}", "boolean"),
        "status": ("={{ Number($json.statusCode) }}", "number"),
        "host": "={{ $('Validate target').item.json.host }}",
        "method": "={{ $('Validate target').item.json.method }}",
        "url": "={{ $('Validate target').item.json.url }}",
        "credential": "Webhook - header auth (ALcredWebhookHdr, header X-Lab-Key)",
        "body": ("={{ $json.body !== null && typeof $json.body === 'object' ? $json.body : { raw: String($json.body ?? '') } }}",
                 "object"),
        "response": "={{ $json.statusCode + ' from ' + $('Validate target').item.json.host + "
                    "(Number($json.statusCode) >= 200 && Number($json.statusCode) < 300 ? ' - shared key accepted' : ' - target rejected the request') }}",
    })
    down = set_fields(wf, "Result (unreachable)", {
        "ok": False,
        "status": 0,
        "host": "={{ $('Validate target').item.json.host }}",
        "method": "={{ $('Validate target').item.json.method }}",
        "url": "={{ $('Validate target').item.json.url }}",
        "credential": "Webhook - header auth (ALcredWebhookHdr, header X-Lab-Key)",
        "body": {},
        "response": "={{ 'connection failed: ' + (typeof $json.error === 'string' ? $json.error : ($json.error && $json.error.message) || 'target unreachable') }}",
    })
    wf.chain(trg, cfg, check, gate)
    wf.connect(gate, call, out=0)
    wf.connect(gate, bad, out=1)
    wf.connect(call, out, out=0)
    wf.connect(call, down, out=1)
    wf.sticky(
        "## P07 - Signed request (credential-backed secret)\n"
        "Call with `{url, method?, body?}`. Returns `{ok, status, host, body, response}`.\n\n"
        "The `X-Lab-Key` value lives **only** in the `Webhook - header auth` credential (created by "
        "`scripts/bootstrap.py` from `LAB_WEBHOOK_KEY` in `.env`). This JSON carries `{id, name}` of that "
        "credential and nothing else - no `$env`, no key in a Set or Code node.\n\n"
        "**Allowed hosts** is the non-secret knob: the key is sent only to hosts listed there; anything else "
        "is refused with Stop and Error before any request is made.",
        pos=(-40, -360), width=640, height=290)
    return wf


if __name__ == "__main__":
    build().save()
