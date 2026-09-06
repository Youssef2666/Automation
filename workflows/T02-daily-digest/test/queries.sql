-- The three summaries T02 runs (lookback window = 30 days). Run: docker compose exec -T postgres psql -U n8n -d demo -f - < test/queries.sql
select status, count(*)::int as orders, coalesce(sum(total), 0)::float as revenue
from orders where ordered_at >= now() - (30 * interval '1 day') group by status order by status;

select priority, count(*)::int as open_tickets,
       round(extract(epoch from (now() - min(created_at))) / 3600)::int as oldest_hours,
       count(*) filter (where sla_due_at < now())::int as sla_breached
from tickets where status in ('open', 'triaged', 'in_progress') group by priority
order by case priority when 'urgent' then 0 when 'high' then 1 when 'medium' then 2 else 3 end;

select count(*)::int as new_customers, count(*) filter (where segment = 'enterprise')::int as enterprise
from customers where created_at >= now() - (30 * interval '1 day');
