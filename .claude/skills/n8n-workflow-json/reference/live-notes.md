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
- **Re-importing an active trigger workflow leaves the old trigger running in the n8n process** (a file drop
  fired 3 executions, one per imported version). `scripts/import-workflows.sh` now deactivates via the API before
  importing; if duplicates already exist, `docker compose restart n8n` clears them.
- Form Trigger v2.2+: the URL path must be in `options.path` (top-level `path` is ignored) -> `/form/<path>`;
  the form only answers while the workflow is active. Submissions post multipart fields `field-0..n` in field order
  and arrive keyed by field label (`$json.Name`, `$json.Email`, ...).
- Crypto (hash) and S3 (upload) nodes output items **without** the binary; every consumer of a file branches
  directly off the Read node. Branch execution order follows node position (v1 order), not connection order.
- `import:workflow --separate` on a batch where two workflows introduce the same NEW tag fails with
  "duplicate key value violates unique constraint" (tag_entity.name); the import script now imports one file per call.
- After `docker compose restart n8n` wait for `/healthz` **and** ~10 s more before importing/running the CLI,
  otherwise imports fail half-way (workflows missing, "does not exist").
- Folders without a README yet are not republished by the import script (no `autopublish` to read): pass
  `--publish` while developing.

## Learned while shipping P04 / D03 (2026-09-07)

- In a Code node placed after a loop/cycle, `$('Node').all()` returns only the **latest run** of that node. To count
  across every turn walk the run index: `for (let r = 0; ; r++) { try { items = $('Node').all(0, r) } catch { break } ... }`.
- A sub-workflow's first Execute Workflow call in an execution costs ~650 ms (cold start); later calls ~150 ms.
  Do not design timing-sensitive demos around "the request happens right after the gate says go".
- Fixed-window gate (P04) vs a server with a **sliding** window (mock-api `/ratelimited`): five gated calls late in
  one window plus five early in the next still trip the server. Deterministic zero-429 demo = gate at half the
  server quota (`limit: 2` per 10 s against 5 per 10 s), plus an 11 s drain after any ungated burst.
- Redis node `incr` accepts expressions for both `key` and `ttl` (`expire: true`); the key is created with the
  TTL on every call, so a fixed-window counter needs `ttl = window + 1`.
- HTML node v1.2 `extractHtmlContent` with `returnValue: attribute` + `attribute: data-sku` and `returnArray: true`
  returns one parallel array per selector (zip them in a Code node). `#catalog` with `returnArray: false` gives a
  single string. Source: HTTP Request `responseFormat: text` -> `{data: "<html>"}` -> `sourceData: json`, `dataPropertyName: data`.
- Merge v3 `numberInputs: 3` works; the Sort node is stable, so ties keep Merge input order (put the source that
  should win ties on input 0). Remove Duplicates v1.1 keeps the first occurrence.
- Postgres node select returns `numeric` as a string and `timestamptz` as an ISO string with `.000Z`; normalize
  timestamps with `new Date(v).toISOString()` before comparing sources.
- Read/Write File `write` keeps the binary on its output item, so `write_file -> s3_upload` chains without a
  re-read. Convert to File `csv` prefixes the file with a UTF-8 BOM (harmless for Excel; strip it for strict parsers).
- Cycles that re-enter a sub-workflow node (Wait -> gate again) work as long as the sub-workflow echoes the fields
  it needs as input (P04 returns `key`, `limit`, `window_seconds`).

## Learned while shipping P08 / O05 / M04 (2026-09-07)

- **Postgres v2.5 `queryReplacement`**: every `{{ }}` is evaluated on its own. An array result pushes its elements
  (objects/arrays are `JSON.stringify`-ed, `undefined` is *dropped* -> "there is no parameter $N"); a plain string
  result is split on commas (a note containing "a, b" becomes two params). Always pass one array:
  `params="={{ [ $json.a, $json.b ?? null, JSON.stringify($json.rows) ] }}"`. A nested array inside that outer
  array reaches pg as `text[]` ("cannot cast type text[] to jsonb") -> pre-stringify it.
- Redis v1 `get` on a missing key outputs `{ "<propertyName>": null }` and **drops the input fields**; read
  earlier data with `$('Node').first().json`.
- Postgres `executeQuery` returning zero rows + `alwaysOutputData` -> one `{}` item; a failed Postgres node with
  `continueRegularOutput` emits `{message, error: {...}}` (pairedItem intact) - test `$json.action === undefined`
  (or whatever column the query returns), not `$json.error`.
- n8n node (`n8n-nodes-base.n8n`): `execution getAll` items have no workflow *name* (`id, mode, status, startedAt,
  stoppedAt, workflowId, finished`); get names from `workflow getAll` (`filters.excludePinnedData: true`) and map.
  `options.activeWorkflows` on execution getAll is the "Include Execution Details" flag (`includeData`).
  The node runs once per input item: `.once()` on the second call, `.always_output()` on the first so an empty
  page does not end the run.
- Paired-item lookups (`$('Config').item`) fail *several nodes after* a Code node that returned fresh items
  ("Paired item data for item from node '...' is unavailable"). After any Code node use `$('Node').first().json`.
- `run-workflow.py` twice in a row can hit "Task Broker's port 5680 is already in use" if the previous CLI process
  has not exited yet; just rerun. The CLI's trailing "Calling Error Workflow ... Cannot use a pool after calling
  end on the pool" is noise: read the real error with `executions.py --id <exec>`.
- Writable-CTE upsert (`with updated as (update ... returning), inserted as (insert ... where not exists (select 1
  from updated) returning) select ...`) works from the Postgres node without a unique index (P08). For bulk
  `INSERT ... ON CONFLICT` with a partial unique index the conflict target must repeat the predicate:
  `on conflict (execution_id) where execution_id is not null do update` (O05). `returning (xmax = 0) as inserted`
  distinguishes inserted from updated rows.
- Redis `INCR` + `expire` refreshes the TTL on every call (P01 semantics: silent while it keeps happening). For
  "at most once per hour" key the counter on the hour: `m04:alert:<metric>:{{ $now.toFormat('yyyyLLddHH') }}`.
