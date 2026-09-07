-- M05: what the workflow compares against, and what it just stored.
-- Last stored rate per quote (the "previous" column in the alert e-mail):
select distinct on (quote) quote, rate, fetched_at
from rate_history
where base = 'USD'
order by quote, fetched_at desc;

-- Rows written by the last two runs (seed rows are dated 06:00 on earlier days):
select quote, rate, fetched_at
from rate_history
where fetched_at > now() - interval '1 day'
order by fetched_at desc, quote
limit 14;

-- Change between the two most recent points per quote, as the Compare node computes it:
with ranked as (
  select quote, rate, fetched_at,
         row_number() over (partition by quote order by fetched_at desc) as rn
  from rate_history where base = 'USD'
)
select a.quote, b.rate as previous, a.rate as now,
       round(((a.rate - b.rate) / b.rate * 100)::numeric, 3) as change_pct
from ranked a join ranked b on a.quote = b.quote and a.rn = 1 and b.rn = 2
order by abs(a.rate - b.rate) / b.rate desc;
