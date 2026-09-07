#!/usr/bin/env python
"""M02 - GitHub Events to Chat Notification.

POST /webhook/m02-github (GitHub-shaped payloads from seed/payloads) -> HMAC-SHA256 signature check
(X-Hub-Signature-256 with the shared LAB_WEBHOOK_KEY, computed in a Code node) -> P03 idempotency on
X-GitHub-Delivery -> route by X-GitHub-Event (push | issues | other) -> one chat-style message
-> e-mail (Mailpit) + notifications row. Telegram is the optional external variant (documented, not required).
Test fixtures: test/*.json bodies + test/send.py that signs and posts them.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, catalog_id, code, cond_bool, crypto_hash, email, execute_workflow, if_,  # noqa: E402
                         postgres_insert, respond, set_fields, switch, webhook)

# The shared secret is the same dummy value as LAB_WEBHOOK_KEY in .env.example. It is NOT read from $env
# (blocked by N8N_BLOCK_ENV_ACCESS_IN_NODE); production would keep it in a credential (see P07 / docs/security.md).
SHARED_SECRET = "lab-demo-key"

VERIFY_JS = r"""
// GitHub signs the raw body: sha256=HMAC_SHA256(secret, body). The Crypto node computed the HMAC over the raw
// binary body (rawBody is enabled on the Webhook node); here we compare it with the header, constant-length.
const req = $('GitHub webhook').first().json;
const headers = req.headers || {};
const expected = 'sha256=' + String($json.hmac || '');
const given = String(headers['x-hub-signature-256'] || '');
let valid = given.length === expected.length;
for (let i = 0; i < expected.length; i++) valid = valid && (given.charCodeAt(i) === expected.charCodeAt(i));
return [{ json: {
  valid, event: String(headers['x-github-event'] || 'unknown'),
  delivery: String(headers['x-github-delivery'] || ''),
  body: req.body || {},
}}];
"""

FORMAT_JS = r"""
// Turn a GitHub event into one chat-style message (Markdown-ish text + HTML).
const ev = $('Verify signature').first().json;
const b = ev.body || {};
const repo = (b.repository && b.repository.full_name) || 'unknown/repo';
let title = '', lines = [], severity = 'info';
if (ev.event === 'push') {
  const branch = String(b.ref || '').replace('refs/heads/', '');
  const commits = Array.isArray(b.commits) ? b.commits : [];
  title = `push to ${repo}@${branch}: ${commits.length} commit(s) by ${(b.pusher && b.pusher.name) || (b.sender && b.sender.login) || '?'}`;
  lines = commits.slice(0, 10).map(c => `${String(c.id || '').slice(0, 7)} ${String(c.message || '').split('\n')[0]}`);
  if (b.forced) { severity = 'warning'; lines.unshift('FORCE PUSH'); }
} else if (ev.event === 'issues') {
  const i = b.issue || {};
  title = `issue ${b.action}: #${i.number} ${i.title} (${repo})`;
  lines = [`by ${(i.user && i.user.login) || '?'}`, ...(i.labels || []).map(l => `label: ${l.name || l}`), (i.html_url || '')];
  if (['opened', 'reopened'].includes(b.action)) severity = 'warning';
} else {
  title = `${ev.event} event on ${repo}`;
  lines = [JSON.stringify(b).slice(0, 200)];
}
const text = [title, ...lines].join('\n');
const html = `<b>${title}</b><br>` + lines.map(l => `&bull; ${l}`).join('<br>');
return [{ json: { event: ev.event, delivery: ev.delivery, repo, title, text, html, severity } }];
"""


def build() -> Workflow:
    wf = Workflow("M02", "github-events-to-chat", "GitHub Events to Chat Notification", tags=["Monitoring"],
                  error_workflow=catalog_id("P01"),
                  description="Signed GitHub webhooks -> verified, deduplicated, routed, and posted as chat-style notifications.")
    hook = webhook(wf, "GitHub webhook", "m02-github", raw_body=True)
    cfg = set_fields(wf, "Config", {"shared_secret": SHARED_SECRET, "notify": "dev@lab.local"}, include_other=True)
    hmac = crypto_hash(wf, "HMAC-SHA256 of raw body", "", prop="hmac", hmac_secret="={{ $json.shared_secret }}")
    hmac.parameters.update({"binaryData": True, "binaryPropertyName": "data"})
    del hmac.parameters["value"]
    verify = code(wf, "Verify signature", VERIFY_JS)
    ok = if_(wf, "Signature valid?", [cond_bool("={{ $json.valid }}")])
    bad = respond(wf, "Respond 401", body='={{ { ok: false, error: "invalid signature" } }}', code=401)
    guard_in = set_fields(wf, "Guard input", {"external_id": "={{ $json.delivery || $json.body.after || 'no-delivery-id' }}",
                                              "scope": "m02-github", "ttl_seconds": 604800})
    guard = execute_workflow(wf, "Idempotency guard (P03)", catalog_id("P03"), cached_name="P03 - Idempotency guard")
    dup = if_(wf, "Duplicate delivery?", [cond_bool("={{ $json.duplicate }}")])
    dup_resp = respond(wf, "Respond 200 (duplicate)", body='={{ { ok: true, duplicate: true } }}')
    route = switch(wf, "Route by event", [
        ("push", [{"leftValue": "={{ $('Verify signature').first().json.event }}", "rightValue": "push",
                   "operator": {"type": "string", "operation": "equals"}}]),
        ("issues", [{"leftValue": "={{ $('Verify signature').first().json.event }}", "rightValue": "issues",
                     "operator": {"type": "string", "operation": "equals"}}]),
    ])
    fmt_push = code(wf, "Format push", FORMAT_JS)
    fmt_issue = code(wf, "Format issue", FORMAT_JS)
    fmt_other = code(wf, "Format other", FORMAT_JS)
    mail = email(wf, "Notify dev channel (Mailpit)", "={{ $('Config').first().json.notify }}",
                 "=[github] {{ $json.title }}", html="={{ $json.html }}").retry(3, 2000)
    note = postgres_insert(wf, "Record notification", "notifications", {
        "channel": "email", "target": "={{ $('Config').first().json.notify }}",
        "subject": "={{ $('Format push').first().json.title || $('Format issue').first().json.title || $('Format other').first().json.title }}",
        "body": "=delivery {{ $('Verify signature').first().json.delivery }}",
        "severity": "info", "sent_at": "={{ $now.toISO() }}",
    }).on_error("continueRegularOutput")
    done = respond(wf, "Respond 202", body='={{ { ok: true, event: $("Verify signature").first().json.event } }}', code=202)
    wf.chain(hook, cfg, hmac, verify, ok)
    wf.connect(ok, guard_in, out=0)
    wf.connect(ok, bad, out=1)
    wf.chain(guard_in, guard, dup)
    wf.connect(dup, dup_resp, out=0)
    wf.connect(dup, route, out=1)
    wf.connect(route, fmt_push, out=0)
    wf.connect(route, fmt_issue, out=1)
    wf.connect(route, fmt_other, out=2)
    for f in (fmt_push, fmt_issue, fmt_other):
        wf.connect(f, mail)
    wf.chain(mail, note, done)
    wf.sticky(
        "## M02 - GitHub events -> chat\n"
        "`POST /webhook/m02-github` with the GitHub headers. The body is verified with **HMAC-SHA256** "
        "(`X-Hub-Signature-256`, shared secret = `LAB_WEBHOOK_KEY`), deduplicated on `X-GitHub-Delivery` (P03, 7 days), "
        "routed by `X-GitHub-Event` (push / issues / other) and posted as one message.\n\n"
        "Core path posts to Mailpit (`dev@lab.local`); add a Telegram node (credential `Telegram - bot`) for a real "
        "chat. `test/send.py` signs and replays the seed payloads.",
        pos=(-40, -360), width=660, height=250)
    return wf


if __name__ == "__main__":
    build().save()
