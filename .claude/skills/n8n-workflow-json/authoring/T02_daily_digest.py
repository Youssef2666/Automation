#!/usr/bin/env python
"""T02 - Scheduled Daily Digest.

Cron 07:00 (workflow timezone Africa/Tripoli) -> Config (lookback window, recipient) -> three Postgres summaries
(orders by status, open tickets by priority, new customers) -> HTML + text digest -> Mailpit -> notifications row.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, catalog_id, code, email, manual_trigger, postgres_insert, postgres_query,  # noqa: E402
                         schedule, set_fields)

ORDERS_SQL = """select status, count(*)::int as orders, coalesce(sum(total), 0)::float as revenue
from orders
where ordered_at >= now() - ($1::int * interval '1 day')
group by status
order by status"""

TICKETS_SQL = """select priority, count(*)::int as open_tickets,
       round(extract(epoch from (now() - min(created_at))) / 3600)::int as oldest_hours,
       count(*) filter (where sla_due_at < now())::int as sla_breached
from tickets
where status in ('open', 'triaged', 'in_progress')
group by priority
order by case priority when 'urgent' then 0 when 'high' then 1 when 'medium' then 2 else 3 end"""

CUSTOMERS_SQL = """select count(*)::int as new_customers,
       count(*) filter (where segment = 'enterprise')::int as enterprise
from customers
where created_at >= now() - ($1::int * interval '1 day')"""

RENDER_JS = r"""
// Build one digest item from the three summary nodes (each ran once, executeOnce).
const cfg = $('Config').first().json;
const orders = $('Orders by status').all().map(i => i.json);
const tickets = $('Open tickets by priority').all().map(i => i.json);
const cust = ($('New customers').first() || { json: {} }).json;
const day = $now.setZone('Africa/Tripoli').toFormat('yyyy-LL-dd');
const money = (n) => Number(n || 0).toLocaleString('en-US', { style: 'currency', currency: 'USD' });
const totalOrders = orders.reduce((a, r) => a + Number(r.orders || 0), 0);
const totalRevenue = orders.filter(r => ['paid', 'shipped'].includes(r.status)).reduce((a, r) => a + Number(r.revenue || 0), 0);
const openTickets = tickets.reduce((a, r) => a + Number(r.open_tickets || 0), 0);
const breached = tickets.reduce((a, r) => a + Number(r.sla_breached || 0), 0);
const empty = totalOrders === 0 && openTickets === 0 && Number(cust.new_customers || 0) === 0;

const rows = (list, cols) => list.length
  ? list.map(r => '<tr>' + cols.map(c => `<td style="padding:4px 10px;border-bottom:1px solid #eee">${r[c] ?? ''}</td>`).join('') + '</tr>').join('')
  : `<tr><td colspan="${cols.length}" style="padding:4px 10px;color:#888">nothing in the window</td></tr>`;
const table = (title, list, cols, headers) => `
<h3 style="margin:18px 0 6px">${title}</h3>
<table style="border-collapse:collapse;font-size:14px"><tr>${headers.map(h => `<th align="left" style="padding:4px 10px;border-bottom:2px solid #333">${h}</th>`).join('')}</tr>${rows(list, cols)}</table>`;

const html = `<div style="font-family:sans-serif;max-width:720px">
<h2>Daily digest - ${day}</h2>
<p>Window: last <b>${cfg.lookback_days}</b> day(s). ${empty ? '<b>Nothing to report.</b>' : ''}</p>
<p><b>${totalOrders}</b> orders, <b>${money(totalRevenue)}</b> paid/shipped revenue · <b>${openTickets}</b> open tickets
(<b>${breached}</b> past SLA) · <b>${cust.new_customers || 0}</b> new customers (${cust.enterprise || 0} enterprise)</p>
${table('Orders by status', orders.map(r => ({ ...r, revenue: money(r.revenue) })), ['status', 'orders', 'revenue'], ['Status', 'Orders', 'Revenue'])}
${table('Open tickets by priority', tickets, ['priority', 'open_tickets', 'oldest_hours', 'sla_breached'], ['Priority', 'Open', 'Oldest (h)', 'Past SLA'])}
<p style="color:#888;font-size:12px">Sent by T02 - Scheduled Daily Digest (Automation Lab). Timezone Africa/Tripoli.</p>
</div>`;

const text = [`Daily digest - ${day} (last ${cfg.lookback_days} days)`,
  `${totalOrders} orders, ${money(totalRevenue)} paid/shipped revenue`,
  `${openTickets} open tickets, ${breached} past SLA`,
  `${cust.new_customers || 0} new customers`,
  ...orders.map(r => `  order ${r.status}: ${r.orders} (${money(r.revenue)})`),
  ...tickets.map(r => `  tickets ${r.priority}: ${r.open_tickets} open, oldest ${r.oldest_hours} h, ${r.sla_breached} past SLA`),
].join('\n');

return [{ json: {
  subject: `[Automation Lab] Daily digest ${day}${empty ? ' - nothing to report' : ''}`,
  to: cfg.report_to, html, text, day, empty,
  totals: { orders: totalOrders, revenue: totalRevenue, open_tickets: openTickets, sla_breached: breached,
            new_customers: Number(cust.new_customers || 0) },
}}];
"""


def build() -> Workflow:
    wf = Workflow("T02", "daily-digest", "Scheduled Daily Digest", tags=["Triggers"],
                  error_workflow=catalog_id("P01"), timezone="Africa/Tripoli",
                  description="Timezone-aware 07:00 cron that e-mails an orders/tickets/customers digest.")
    trg = schedule(wf, "Every day 07:00", cron="0 7 * * *")
    manual = manual_trigger(wf, "Run once (manual / CLI)")
    cfg = set_fields(wf, "Config", {"lookback_days": 30, "report_to": "ops@lab.local"})
    q1 = postgres_query(wf, "Orders by status", ORDERS_SQL, params="={{ $('Config').item.json.lookback_days }}").once().always_output()
    q2 = postgres_query(wf, "Open tickets by priority", TICKETS_SQL).once().always_output()
    q3 = postgres_query(wf, "New customers", CUSTOMERS_SQL, params="={{ $('Config').item.json.lookback_days }}").once().always_output()
    render = code(wf, "Render digest", RENDER_JS)
    mail = email(wf, "Send digest (Mailpit)", "={{ $json.to }}", "={{ $json.subject }}", html="={{ $json.html }}").retry(3, 2000)
    log = postgres_insert(wf, "Record notification", "notifications", {
        "channel": "email",
        "target": "={{ $('Render digest').item.json.to }}",
        "subject": "={{ $('Render digest').item.json.subject }}",
        "body": "={{ $('Render digest').item.json.text }}",
        "severity": "info",
        "sent_at": "={{ $now.toISO() }}",
    }).on_error("continueRegularOutput")
    wf.chain(trg, cfg, q1, q2, q3, render, mail, log)
    wf.connect(manual, cfg)
    wf.sticky(
        "## T02 - Daily digest\n"
        "Cron `0 7 * * *` evaluated in the **workflow timezone** (Settings -> Timezone = Africa/Tripoli), "
        "so 07:00 local, DST-safe.\n\n"
        "`Config` holds the lookback window (30 days, because the seed data is dated around 2026-09-01) and the "
        "recipient. The three queries run once each (Execute Once) and the Code node renders HTML + text.\n\n"
        "Check Mailpit at http://localhost:8025 and the `notifications` table.",
        pos=(-40, -330), width=640, height=230)
    return wf


if __name__ == "__main__":
    build().save()
