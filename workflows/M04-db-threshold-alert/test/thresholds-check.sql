-- M04: the metrics query the workflow runs (node "Current values"), verbatim. Run it to know which limits the
-- seed data trips before executing the workflow. Limits (node "Thresholds"): pending_orders_max 10,
-- pending_age_hours_max 48, open_tickets_urgent_max 3, sla_breached_max 0. With the seed, pending_orders,
-- pending_age_hours and sla_breached are over; open_tickets_urgent (1) is within.
select
  (select count(*) from orders where status = 'pending')::int as pending_orders,
  (select coalesce(round(extract(epoch from (now() - min(ordered_at))) / 3600), 0)
     from orders where status = 'pending')::int as pending_age_hours,
  (select count(*) from tickets
     where status in ('open', 'triaged', 'in_progress') and priority = 'urgent')::int as open_tickets_urgent,
  (select count(*) from tickets
     where status in ('open', 'triaged', 'in_progress') and sla_due_at < now())::int as sla_breached,
  now() as checked_at;

-- Which tickets are past SLA (the detail behind sla_breached):
select ticket_no, priority, status, sla_due_at, round(extract(epoch from (now() - sla_due_at)) / 3600) as hours_over
from tickets
where status in ('open', 'triaged', 'in_progress') and sla_due_at < now()
order by sla_due_at
limit 10;
