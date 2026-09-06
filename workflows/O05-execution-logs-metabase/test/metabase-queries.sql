-- Metabase questions for the "Automation Lab - runs" dashboard (database `demo`, host `postgres`).
-- Paste each block into Metabase -> New -> SQL query, save, then pin to a dashboard. Same SQL as
-- docs/observability.md so the two stay in sync.

-- 1. Runs per workflow and status, last 24 h (bar chart, stacked by status)
select workflow_name, status, count(*) as runs
from execution_log
where coalesce(started_at, logged_at) > now() - interval '24 hours'
group by workflow_name, status
order by workflow_name, status;

-- 2. Duration p50 / p95 per workflow (table; sort by p95 to find the slow ones)
select workflow_name,
       round(percentile_cont(0.5) within group (order by duration_ms))::int  as p50_ms,
       round(percentile_cont(0.95) within group (order by duration_ms))::int as p95_ms,
       count(*) as runs
from execution_log
where duration_ms is not null
group by workflow_name
order by p95_ms desc;

-- 3. Failures and warnings per workflow, last 7 days (row chart)
select workflow_name,
       count(*) filter (where status = 'error')   as errors,
       count(*) filter (where status = 'warning') as warnings
from execution_log
where status in ('error', 'warning') and coalesce(started_at, logged_at) > now() - interval '7 days'
group by workflow_name
order by errors desc, warnings desc;

-- 4. Alert volume per day, channel and severity (alert-fatigue check; line chart by day)
select date_trunc('day', sent_at) as day, channel, severity, count(*) as sent
from notifications
group by 1, 2, 3
order by 1 desc, 4 desc;

-- 5. Runs per hour, last 24 h (line chart; a flat zero means a schedule stopped)
select date_trunc('hour', coalesce(started_at, logged_at)) as hour, count(*) as runs
from execution_log
where coalesce(started_at, logged_at) > now() - interval '24 hours'
group by 1
order by 1;
