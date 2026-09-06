-- O05 schema add-on: the unique index the workflow creates on its first run (idempotent, safe to re-run).
-- execution_log itself comes from seed/schema.sql. The index lets O05 bulk-insert with ON CONFLICT and
-- makes "one row per execution" a database guarantee rather than a convention.
create unique index if not exists execution_log_execution_id_uidx
  on execution_log (execution_id)
  where execution_id is not null;

-- Pre-condition check: the index cannot be created while duplicate execution_ids exist (only possible if rows
-- were inserted by hand before O05 ever ran). Expect zero rows:
select execution_id, count(*) as rows
from execution_log
where execution_id is not null
group by execution_id
having count(*) > 1;

-- If the check returned rows, keep the newest row per execution_id and re-run the create index:
-- delete from execution_log a using execution_log b
--  where a.execution_id = b.execution_id and a.id < b.id;
