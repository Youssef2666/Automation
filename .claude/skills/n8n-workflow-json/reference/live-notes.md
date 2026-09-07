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

## Learned while shipping P05 (2026-09-07)

- **Execute Workflow v1.1 + `onError: continueErrorOutput` is only dependable with ONE item per call.** Eleven
  probes against `ALP05SubWorkflow` (`mode: each`, lane wired; lanes = items per output branch):
  `1 bad -> [0,1]`, `1 good -> [1,0]`, `2 good -> [2,0]`, `4 good -> [4,0]` (all correct - a batch is fine when
  nothing fails); `2 bad -> [0,1]` (only the first failure survives); `1 good + 1 bad -> [1,0]` (**the failing
  item is dropped and the node reports success** - the dangerous one); `2 good + 1 bad (bad last) -> [2,0,1]`
  (a **third** branch appears and the error is on index 2); `1 bad + 2 good (bad first) -> [2,1]`;
  `3 good + 1 bad -> [4]` (crash: `TypeError: Cannot read properties of undefined (reading 'entries')` in
  `WorkflowExecute.assignPairedItems`, the raw inputs echoed on branch 0). `mode: once` with one bad item is
  correct (`[0,1]`), and one bad item with no lane fails the caller, as designed. So the branch count and the
  error's index move with the batch size *and* the failing item's position - no fixed index and no formula.
  Rule: keep the error lane only on single-item calls; batch either work that cannot fail, or without the lane.
  A caller forced to batch must read *every* branch after 0 (what `patterns/P07-secrets/test/run.py` does).
  The sub-workflow itself is fine in every case (one integrated execution per item, right result / Stop and Error).
- A sub-workflow's `settings.errorWorkflow` fires even when the caller handles the failure on its error lane: a
  refused P05 call produced an integrated execution with status `error` **and** a P01 execution in `mode: error`
  that wrote the `execution_log` row (`error_message`, `error_node`). Blocks that are expected to reject input
  regularly will page someone; validate in the caller, and keep "not found" as data (`ok: true, found: false`).
- Manual-trigger-only harnesses cannot be activated (`publish.py` / the UI toggle refuse them: no trigger node).
  Run them with `scripts/dev/run-workflow.py <id>`; the sub-workflow they call must still be published.

## Learned while shipping A02 (2026-09-07, Ollama + LangChain nodes)

- **Structured Output Parser v1.2 (`schemaType: fromJson`) wraps your example in an `output` envelope.** It asks
  the model for `{"output": {...your fields...}}`; an answer with the bare object parses into `{}` (zod strips
  unknown keys) and the chain node emits `{}` - i.e. `{output: undefined}` - **with no error**. A correct
  classification silently becomes an empty one, so validate the *values* downstream instead of trusting that a
  green node means data. Measured with `llama3.2:3b` on four tickets: 2/4 answers wrapped when only the parser's
  format instructions ask; **4/4** after adding one sentence to the system message
  (`Return one JSON object with exactly one key, "output", holding ...`). Successful parses arrive as
  `{"output": {...}}` -> read `$json.output`.
- The raw model text is visible in the execution: `runData['<model node>'][run].data.ai_languageModel[0][0].json
  .response.generations[0][0].text` (plus `tokenUsageEstimate`). That is how you tell "the model was wrong" from
  "the parser threw the answer away".
- `lmChatOllama` v1 `options.format = "json"` (builder: `ollama_chat(..., json_mode=True)`) works and keeps the
  answer syntactically valid JSON, but says nothing about the *schema*: enums, ranges and required fields still
  need a Code node.
- `chainLlm` v1.4 with `onError: continueErrorOutput` behaves per item; combined with a `splitInBatches`
  batch size of 1 (P05's single-item rule) each ticket fails on its own. `retryOnFail: 2` on the chain node is a
  cheap re-sample of a model that rambled - it does not appear as a second run in the execution data.
- CPU inference in this stack: ~19 s per short classification with `llama3.2:3b` (76 s for four, one at a time).
  `OLLAMA_NUM_PARALLEL: 1`, so two agents driving Ollama serialise - a slow execution may be queueing, not stuck.
- Probing the model directly is much faster than iterating through n8n:
  `POST http://localhost:11434/api/chat {model, format: "json", stream: false, options: {temperature: 0},
  messages: [{role: "system", ...}, {role: "user", ...}]}`.

## Learned while shipping A03 (2026-09-07)

- **Whisper ASR web service** (`onerahmet/openai-whisper-asr-webservice:v1.10.0`, `ASR_ENGINE=faster_whisper`),
  read from the container's own `/app/app/webservice.py` + `utils.py` and confirmed with curl: `POST /asr`,
  multipart field **`audio_file`**, query `encode|task|language|initial_prompt|vad_filter|word_timestamps|output`
  (`output` default `txt`, use `json`). Every response is a `StreamingResponse` with **`content-type:
  text/plain`** and `content-disposition: attachment` *even for `output=json`* -> set the HTTP node to
  `responseFormat: text` (builder `response="text"`) and `JSON.parse($json.data)`. Body:
  `{language, segments: [asdict(faster_whisper.Segment)], text}`; Segment = `id, seek, start, end, text, tokens,
  avg_logprob, compression_ratio, no_speech_prob, words, temperature` (drop `tokens`, it is long and useless).
  Response header `asr-engine` names the engine. 30 s of audio on `base`/int8/CPU = ~11 s.
- **`seed/files/meeting-clip.wav` is now real TTS speech** (66 s, 16 kHz mono): `generate_seed.py` renders an
  invented standup through a local offline engine, so it transcribes stably - three runs gave identical text,
  162 words / 16 segments, mean `avg_logprob` -0.12, mean `no_speech_prob` 0.171, zero verbatim repeats. It used
  to be formant tones, and Whisper hallucinated a different sentence every run (`This is what you want.`,
  `I'm sorry.` x5, `So Oh`, `. . .`); a machine with no TTS engine still gets that tone fallback, and the
  generator warns loudly on stderr when it does. Useful signals for a quality gate, all in the response: word
  count, mean `no_speech_prob`, mean `avg_logprob` (real speech ~-0.1 to -0.3, the tone fallback -0.9 to -1.4)
  and the share of segments whose text repeats verbatim (a decoding loop). Speaker names must be *spoken* in the
  script - Whisper does not diarise, so "Nadia." at the head of a line is the only thing that attributes it.
- **Read/Write File (read) is a glob**: a path matching nothing returns **zero items**, every later node is
  skipped and the execution ends *green* having done nothing (no P08 row either). `alwaysOutputData` on the read
  node plus an If on `={{ $binary?.data?.fileName }}` -> Stop and Error turns that silence into a real failure.
- Output shapes, measured: **Summarization Chain v2** -> `{response: {text: "..."}}`; **Information Extractor v1**
  -> `{output: {...}}`. Summarization Chain v2 `operationMode: nodeInputJson` uses `JSON.stringify(item.json)` as
  the document, so feed it a Set node with only the text field. Its custom prompts
  (`options.summarizationMethodAndPrompts.values.combineMapPrompt` / `.prompt`) are LangChain templates and must
  contain `{text}`.
- Information Extractor is prompt-sensitive in a boring way: "owner and due date only when they were actually
  said" produced **no** `due` values at all from `llama3.2:3b`; "copy `due` exactly as it was spoken (today,
  tomorrow morning, by Friday...)" produced them for every task. Resolve the dates in a Code node afterwards -
  a 3B model cannot do calendar arithmetic, and it also mis-attributes owners in multi-speaker text.
- Idempotent "replace this source's rows" in one Postgres statement, no unique index needed:
  `with cleared as (delete from tasks where source = $1 returning 1), incoming as (select * from
  json_populate_recordset(null::tasks, $2::json)) insert into tasks (...) select $1, ... from incoming returning
  ...`. `json_populate_recordset` casts ISO strings to `date` and ignores JSON keys the table does not have.
  Pair with `.always_output()` so an empty array still lets the flow continue.
- `n8n execute --id` also refuses `N8N_RUNNERS_BROKER_PORT=5680` when another agent's CLI run holds it; any free
  port works (`5684`). Deleting a workflow through `DELETE /api/v1/workflows/<id>` deletes its executions too, so
  capture what a throwaway probe printed *before* removing it.

## Learned while shipping A01 (2026-09-07, Qdrant + RAG)

- **The Qdrant Vector Store node is broken in this image - do not use it.** `vectorStoreQdrant` (any mode) fails
  with `TypeError: fetch failed`; the container log shows the real cause,
  `InvalidArgumentError: invalid onError method` thrown from
  `@qdrant/openapi-typescript-fetch/.../fetcher.js:135`. `@qdrant/js-client-rest@1.16.2` builds an
  **undici 6.28** `Agent` (`dispatcher.js`) and passes it as `init.dispatcher` to Node **26.5**'s built-in
  `fetch`, whose handler uses undici 7's API - the old Agent rejects it. Reproduced in the main process and in
  the `n8n execute` CLI, against a Qdrant that `wget` and the HTTP Request node reach fine from the same
  container. Same root cause will hit any node bundling that client. Workaround used by A01: drive Qdrant over
  its REST API with HTTP Request nodes (scroll / points/delete / PUT collection / points upsert / points/search)
  and write the same payload shape the LangChain node writes (`{content, metadata:{...}}`,
  `contentPayloadKey`/`metadataPayloadKey` defaults), so the node can be swapped back in later over the same
  collection. `lmChatOllama` + `chainLlm` are unaffected.
- **n8n 2.x runs the *published* version of a workflow.** `PATCH /rest/workflows/<id>` (and the editor) changes
  the draft only: an active trigger keeps executing the old nodes until you publish
  (`scripts/dev/publish.py <id>`, or `import-workflows.sh --publish`). Deactivate/activate does **not** promote
  the draft. This is what makes "I changed the model name and nothing happened" look like caching.
- Ollama's batch embedding endpoint is much faster than one call per chunk:
  `POST http://ollama:11434/api/embed {model, input: ["...", "..."]}` -> `{embeddings: [[768 floats], ...]}`,
  in input order. 3 texts in 0.15 s; 177 chunks of ~900 chars in ~60 s in batches of 16 (`nomic-embed-text`,
  137M, CPU). The older `/api/embeddings` takes a single `prompt` instead.
- An HTTP Request node runs **once per input item**, so "one Code node that emits one item per batch" is a
  complete batching mechanism - no Loop Over Items needed. Re-pair the responses with their batches by index
  (`$('Chunk and batch').all()[i]` vs `$input.all()[i]`); paired-item lookups are not reliable one node after a
  Code node that returned fresh items.
- Qdrant point ids must be an unsigned int or a **UUID-shaped** hex string (`8-4-4-4-12`); Qdrant's parser does
  not check the version nibble, so `sha256.slice(0,20)` + `chunkIndex.toString(16).padStart(12,'0')` is a legal,
  deterministic id. That alone makes an upsert idempotent - re-indexing identical content cannot duplicate it.
  `points/delete` by payload filter (`{key: "metadata.source", match: {any: [...]}}`) needs no payload index at
  this scale. `PUT /collections/<name>` on an existing collection answers 4xx `Collection ... already exists!`,
  so pair it with `neverError`.
- `Read/Write File` (read) puts the **directory** on the binary metadata (`binary.data.directory` +
  `.fileName`), not on the json (json only gets `mimeType, fileType, fileName, fileExtension, fileSize`). With a
  glob over several directories that is the only way to tell `patterns/P01-.../README.md` from
  `patterns/P02-.../README.md`. `Extract From File` with `options.keepSource: "both"` keeps the binary next to
  the extracted text, so one Code node afterwards sees both; `destinationKey` is a **top-level** parameter of
  that node, not an option.
- `alwaysOutputData` and `onError: continueErrorOutput` on the same node fight each other: the empty placeholder
  item appears on output 0 **and** the error item on output 1, so both branches run. Pick one.
- Chat Trigger v1.1 with `options.responseMode: "responseNode"` works with a plain
  `n8n-nodes-base.respondToWebhook` node (`respondWith: json`, `responseBody: "={{ $json }}"`), and the flow
  keeps running after it - which is how a chat workflow can log to P08 *after* answering. The production URL is
  `POST /webhook/<webhookId>/chat` with `{action:"sendMessage", sessionId, chatInput}`; the builder derives
  `webhookId` from the workflow id, so the URL is stable across re-imports.
- CPU RAG latency measured here: retrieval (embed + Qdrant search) under 200 ms; the answer is all generation -
  8 s warm and short, ~30 s typical, 43 s when `llama3.2:3b` has to load. `numCtx` defaults to **2048** on
  `lmChatOllama`, which silently truncates four 900-char passages plus a system prompt; A01 sets 4096.
  `keepAlive: "30m"` on the node keeps the model resident between questions.
