#!/usr/bin/env python
"""M03 - RSS Keyword-filtered Digest.

Daily (or manual) -> RSS Read (mock-api /feed.xml, 25 posts) -> keyword filter (Code: title + description,
case-insensitive, records which keyword matched) -> Remove Duplicates across previous executions (by guid,
so each post is digested once ever) -> digest e-mail -> notifications row. No new matches -> quiet run.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, aggregate, catalog_id, code, cond_num, dedupe_prev_runs, email, if_,  # noqa: E402
                         manual_trigger, noop, postgres_insert, rss, schedule, set_fields)

FILTER_JS = r"""
// Keep items whose title or description mentions at least one keyword; annotate with the matches.
const keywords = String($('Config').first().json.keywords || '').split(',').map(k => k.trim().toLowerCase()).filter(Boolean);
const out = [];
for (const it of $input.all()) {
  const j = it.json;
  const hay = `${j.title || ''} ${j.contentSnippet || j.content || j.description || ''}`.toLowerCase();
  const hits = keywords.filter(k => hay.includes(k));
  if (hits.length) out.push({ json: { guid: j.guid || j.id || j.link, title: j.title, link: j.link,
                                      published: j.isoDate || j.pubDate || null, matched: hits,
                                      snippet: String(j.contentSnippet || j.content || '').slice(0, 220) } });
}
return out;
"""

DIGEST_JS = r"""
const items = $input.first().json.data || [];
const cfg = $('Config').first().json;
const li = items.map(i => `<li><a href="${i.link}">${i.title}</a> <small>(${(i.matched || []).join(', ')})</small><br><span style="color:#666">${i.snippet}</span></li>`).join('');
return [{ json: {
  count: items.length,
  subject: `[Automation Lab] RSS digest: ${items.length} new post(s) matching ${cfg.keywords}`,
  html: `<div style="font-family:sans-serif"><h3>${items.length} new post(s)</h3><p>Keywords: <code>${cfg.keywords}</code></p><ul>${li}</ul><p style="color:#888">M03 - only posts not seen in previous runs.</p></div>`,
  text: items.map(i => `- ${i.title} (${i.link})`).join('\n'),
}}];
"""


def build() -> Workflow:
    wf = Workflow("M03", "rss-keyword-digest", "RSS Keyword-filtered Digest", tags=["Monitoring"],
                  error_workflow=catalog_id("P01"),
                  description="Reads an RSS feed, keeps posts matching keywords, digests only the ones never sent before.")
    trg = schedule(wf, "Every day 08:00", cron="0 8 * * *")
    manual = manual_trigger(wf, "Run once (manual / CLI)")
    cfg = set_fields(wf, "Config", {"feed": "http://mock-api:8080/feed.xml",
                                    "keywords": "automation, n8n, ocr, arabic, webhook",
                                    "report_to": "ops@lab.local"})
    feed = rss(wf, "Read feed", "={{ $json.feed }}").retry(3, 2000)
    filt = code(wf, "Keyword filter", FILTER_JS)
    fresh = dedupe_prev_runs(wf, "Only never-sent posts", "={{ $json.guid }}", scope="workflow", history=5000)
    agg = aggregate(wf, "Collect matches")
    any_ = if_(wf, "Anything new?", [cond_num("={{ ($json.data || []).length }}", "gt", 0)])
    digest = code(wf, "Render digest", DIGEST_JS)
    mail = email(wf, "Send digest (Mailpit)", "={{ $('Config').item.json.report_to }}", "={{ $json.subject }}",
                 html="={{ $json.html }}").retry(3, 2000)
    note = postgres_insert(wf, "Record notification", "notifications", {
        "channel": "email", "target": "={{ $('Config').item.json.report_to }}",
        "subject": "={{ $('Render digest').item.json.subject }}", "body": "={{ $('Render digest').item.json.text }}",
        "severity": "info", "sent_at": "={{ $now.toISO() }}",
    }).on_error("continueRegularOutput")
    quiet = noop(wf, "Nothing new")
    wf.chain(trg, cfg, feed, filt, fresh, agg, any_)
    wf.connect(manual, cfg)
    wf.connect(any_, digest, out=0)
    wf.connect(any_, quiet, out=1)
    wf.chain(digest, mail, note)
    agg.always_output()
    wf.sticky(
        "## M03 - RSS keyword digest\n"
        "`RSS Read` on the mock feed (25 posts) -> keyword filter -> **Remove Duplicates (previous executions)** keyed "
        "by guid, so a post is digested once ever, even across manual runs -> one e-mail with the new matches.\n\n"
        "Second run: nothing new. To reset, clear the node's dedupe history in the editor (Remove Duplicates -> Manage Key Values).",
        pos=(-40, -330), width=620, height=210)
    return wf


if __name__ == "__main__":
    build().save()
