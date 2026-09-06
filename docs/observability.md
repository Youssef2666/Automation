# Observability: what you can see, and where

Everything below works offline with the `core` profile; Metabase needs the `observability` profile. The moving parts
are two patterns (P01, P08), one workflow (O05), one Postgres table, and n8n's own execution store.

## The pieces

| Piece | What it does | Where to look |
|---|---|---|
| **P01 error handler** | Every workflow sets `settings.errorWorkflow` to P01. On failure P01 receives the failing workflow, node, error message and execution id, writes a row to `demo.notifications` and emails `ops@lab.local` through Mailpit | `patterns/P01-*`, Mailpit at <http://localhost:8025>, `select * from notifications order by sent_at desc` |
| **P08 execution log** | A sub-workflow called at the end of a run (and by P01 on failure) that upserts one row per execution into `demo.execution_log`: workflow id and name, status, start/finish, duration, error node and message | `patterns/P08-*` |
| **O05 execution stats** | Scheduled workflow that pulls executions from the n8n public API (`n8n API - local` credential), backfills `execution_log` for workflows that do not call P08, and emails a daily summary | `workflows/O05-*` |
| **n8n executions view** | Per-workflow run history with node-level input and output | <http://localhost:5678/home/executions> |
| **Metabase** | Dashboards over `demo.execution_log`, `notifications`, `uptime_checks` | <http://localhost:3001> |
| **Container logs** | n8n's structured log (level from `N8N_LOG_LEVEL`, default `info`) | `docker compose logs -f n8n` |

## Table: `demo.execution_log`

| column | meaning |
|---|---|
| `execution_id` | n8n execution id (unique) |
| `workflow_id`, `workflow_name` | the 16-char deterministic id and the `<ID> - <Title>` name |
| `status` | `success`, `error`, `crashed`, `waiting`, `canceled` |
| `started_at`, `finished_at`, `duration_ms` | timing |
| `error_node`, `error_message` | populated by P01 on failure |
| `logged_at` | when the row was written |

The demo schema is created on first boot from `seed/schema.sql`; `bash scripts/reseed.sh` recreates it (empties the
log). The table is deliberately in the `demo` database, not n8n's own, so dashboards never touch n8n internals.

## Metabase in five minutes

```bash
docker compose --profile core --profile observability up -d
```

1. Open <http://localhost:3001>, create the admin user (any `@lab.local` address).
2. Add a database: PostgreSQL, host `postgres`, port `5432`, database `demo`, user and password from
   `POSTGRES_USER` / `POSTGRES_PASSWORD` in your `.env`.
3. Save these as questions and pin them to a dashboard.

Failures per workflow, last 7 days:

```sql
select workflow_name, count(*) as failures
from execution_log
where status <> 'success' and started_at > now() - interval '7 days'
group by workflow_name order by failures desc;
```

p50 / p95 duration per workflow:

```sql
select workflow_name,
       percentile_cont(0.5) within group (order by duration_ms) as p50_ms,
       percentile_cont(0.95) within group (order by duration_ms) as p95_ms,
       count(*) as runs
from execution_log
where finished_at is not null
group by workflow_name order by p95_ms desc;
```

Alert volume by channel and severity (alert fatigue check):

```sql
select date_trunc('day', sent_at) as day, channel, severity, count(*)
from notifications
group by 1, 2, 3 order by 1 desc, 4 desc;
```

Uptime state right now (M01):

```sql
select target, state, failures, since, last_alert_at from uptime_state order by target;
```

## n8n execution retention

Set in `docker-compose.yml` (`x-n8n-env`):

| Variable | Value | Effect |
|---|---|---|
| `EXECUTIONS_DATA_SAVE_ON_SUCCESS` / `_ON_ERROR` | `all` | keep full node data for every run so you can inspect it in the UI |
| `EXECUTIONS_DATA_SAVE_MANUAL_EXECUTIONS` | `true` | manual test runs are kept too |
| `EXECUTIONS_DATA_PRUNE` | `true` | prune old executions |
| `EXECUTIONS_DATA_MAX_AGE` | `168` | hours (7 days) before an execution is pruned |
| `N8N_DEFAULT_BINARY_DATA_MODE` | `filesystem` | binary payloads go to the `n8n_data` volume, not the database |

For a busy production instance you would save success data as `none`, keep errors, and lower the max age; the
lab keeps everything because you are supposed to click through it. `execution_log` survives pruning, which is the
point of P08: the metrics outlive the raw execution data.

## Where logs live

| Log | Location | Notes |
|---|---|---|
| n8n process log | `docker compose logs n8n` | JSON lines; raise `N8N_LOG_LEVEL=debug` in `.env` when chasing an import problem |
| n8n executions | Postgres `n8n` database, tables `execution_entity` and `execution_data` | pruned per the table above |
| Binary data | volume `n8n_data` (`/home/node/.n8n/binaryData`) | pruned with the execution |
| Workflow outputs | `./data/out` (host) = `/home/node/.n8n-files/data/out` (container) | git-ignored |
| Mail | Mailpit at <http://localhost:8025> (`MP_MAX_MESSAGES=2000`) and its REST API `/api/v1/messages` | GreenMail keeps IMAP inboxes in memory only |
| Bootstrap | `bash scripts/setup.sh` prints each step; `python scripts/dev/executions.py --workflow <ID> --last 5` lists recent runs | |

## Limits

- No metrics endpoint is scraped. n8n can expose Prometheus metrics (`N8N_METRICS=true`), but the lab has no
  Prometheus or Grafana; Metabase over Postgres is enough to show the idea and costs one container.
- No distributed tracing across sub-workflows; `execution_id` of the parent is carried as a field by P05 and P08.
- Log streaming to external sinks is an n8n paid feature and out of scope.
