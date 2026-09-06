# Live-verified notes (n8n 2.37.10 in this stack) - read before building anything

Facts proven against the running stack on 2026-09-06. Add to this file whenever you learn something the hard way.

## Environment / tooling

- Git Bash mangles `/container/paths` in `docker compose exec|cp` arguments. The repo scripts export
  `MSYS_NO_PATHCONV=1`; do the same (`export MSYS_NO_PATHCONV=1`) in any ad-hoc `docker compose exec` command.
- `n8n execute --id=<id>` inside the container needs a different task-broker port:
  `docker compose exec -T -e N8N_RUNNERS_BROKER_PORT=5680 n8n n8n execute --id=...` (`scripts/dev/run-workflow.py`
  and `_n8n.n8n_cli()` already do this).
- **Manual and CLI executions never trigger the error workflow** (n8n behaviour, plus the CLI closes its DB pool
  before P01 could run). To prove P01 wiring use a webhook/schedule execution.
- `bash scripts/import-workflows.sh <folder> [--publish]` is safe to run in parallel (unique staging dir).
  `python scripts/dev/publish.py <workflow-id>` activates one workflow; `python scripts/dev/executions.py --last N`
  lists executions (`--workflow <id>` filters); `python scripts/dev/run-workflow.py <ID>` executes a manual-trigger
  workflow via the CLI.
- Re-importing the same id overwrites the workflow in place (`import:workflow` upserts by id) **and deactivates it**.
  Sub-workflows must be published too ("Workflow is not active and cannot be executed" otherwise).
  `scripts/import-workflows.sh` re-publishes `--publish` targets and folders with `autopublish: true`.
- The demo db is reachable with `docker compose exec -T postgres psql -U n8n -d demo -tAc "<sql>"`;
  `bash scripts/reseed.sh` **drops and recreates** the demo db (wipes execution_log/notifications rows) - do not run
  it while another agent is verifying.
- Mailpit API: `curl -s "localhost:8025/api/v1/messages?limit=5"`; Redis: `docker compose exec -T redis redis-cli ...`;
  mock-api: `curl -s localhost:8080/health`, `/products?_limit=1`, `/events?_start=0&_limit=50` (json-server 0.17 query
  syntax: `_page`, `_limit`, `_sort`, `_order`, `field_gte=`), plus the middleware routes in
  `docker/mock-api/middleware.js`.
- Do **not** run `python scripts/build-matrix.py` from a builder agent (README.md is shared); the orchestrator does.

## Node behaviour

- Error Trigger item shape: `{execution:{id,url,retryOf,error:{message,stack},lastNodeExecuted,mode,startedAt},
  workflow:{id,name}}` or `{trigger:{error,mode,...}, workflow}` when a trigger failed to start.
- Stop and Error message text is rewritten by n8n when it contains `ECONNREFUSED` ("The service refused the
  connection..."); do not assert on such messages.
- Redis node v1 `incr` outputs `{ "<key>": <number> }` (key name is dynamic) -> read it with
  `{{ Number(Object.values($json)[0]) }}`. `expire: true, ttl: N` sets the TTL.
- Postgres node v2.5 `insert` with `columns.mappingMode = defineBelow` and `value = {column: expression}` works with
  `identity` PK tables (omit the id). Use `.on_error("continueRegularOutput")` on log/audit inserts.
- Webhook node v2 with `responseMode: onReceived` answers `{"message":"Workflow was started"}` immediately;
  `responseNode` requires a Respond to Webhook node on every path (including error/validation paths).
- Expressions referencing earlier nodes: `$('Node Name').item.json.field` (paired item) - works after Postgres and
  Redis nodes because they keep pairedItem. After a Code node that returns new items use `$('Node').first().json`.
- Workflow-level `settings.errorWorkflow` is respected on import; P01 (`ALP01ErrorHandle`) must be active.

## Proven flows

- P01: webhook execution fails -> P01 runs (`mode=error`), execution_log row, Redis counter, Mailpit e-mail.
- `n8n execute --id` fails with "Missing node to start execution" unless the workflow has a **Manual Trigger**.
  Schedule-driven workflows therefore also get `manual_trigger(wf, "Run once (manual / CLI)")` wired to the same
  first node (n8n's usual "When clicking Test workflow" convention).
- Set node v3.4 typed fields are strict: `object` rejects arrays and `null` handling varies -> build result
  objects that may carry arbitrary JSON with a Code node instead.
- HTTP Request v4.2 with `fullResponse` + `neverError`: output `{body, headers, statusCode, statusMessage}`; only
  connection-level failures go to the error output (`continueErrorOutput`) as `{error:{message,...}}`.
- `$('Node').last().json` works inside loops (returns the latest run of that node).
- **Boolean node parameters do not evaluate expressions** (e.g. HTTP Request `sendBody: "={{ ... }}"` sent no
  body at all, silently). Use static `True/False` and put the dynamic part in the value (`jsonBody`).
- HTTP Request `specifyHeaders: json` + `jsonHeaders: ={{ JSON.stringify(obj) }}` and `specifyBody: json` +
  `jsonBody: ={{ JSON.stringify(obj) }}` work; hostnames `n8n:5678`, `mock-api:8080` resolve inside the network.
- Code node `runOnceForEachItem` + `$('Earlier node').item.json` pairs correctly even when the input comes from
  an HTTP node's error output.
