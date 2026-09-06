---
name: n8n-workflow-json
description: How to author import-ready n8n 2.x workflow JSON for this repo - the builder DSL, node/typeVersion allowlist, expression syntax, credential references, connections (main + AI lanes), and the verification loop. Load before creating or editing any workflow.json.
---

# n8n workflow JSON (n8n 2.37, Community Edition)

The shipped artifact is `workflow.json` in every `workflows/<ID>-<slug>/` and `patterns/<ID>-<slug>/` folder.
It must import into a clean n8n 2.37 instance via `n8n import:workflow` with **zero manual fixes**.

## 1. Author with the builder, not by hand

`n8n_builder.py` (this folder) is a tiny DSL that emits correct JSON. One authoring script per workflow lives in
`authoring/<ID>_<slug>.py`. Run it to (re)generate the JSON:

```bash
python .claude/skills/n8n-workflow-json/authoring/T01_webhook_to_database.py
```

Minimal script:

```python
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from n8n_builder import *

wf = Workflow("T01", "webhook-to-database", "Webhook to Database", tags=["Triggers"],
              error_workflow=catalog_id("P01"))
hook = webhook(wf, "Webhook", path="t01-orders")                       # POST /webhook/t01-orders
check = if_(wf, "Valid payload?", [cond_exists("={{ $json.body.email }}")])
save = postgres_insert(wf, "Insert order", "webhook_events")           # autoMap input -> columns
ok = respond(wf, "Respond 200", body='={{ { "ok": true, "id": $json.id } }}')
bad = respond(wf, "Respond 400", body='{"ok": false, "error": "email required"}', code=400)
wf.chain(hook, check, save, ok)
wf.connect(check, bad, out=1)                                          # false branch
wf.sticky("## T01\nWebhook -> validate -> Postgres -> respond", pos=(-300, -200))
wf.save()                                                              # -> workflows/T01-webhook-to-database/workflow.json
```

Rules the builder enforces: deterministic ids (`wf_id(code, slug)`, 16 chars), credentials as `{id, name}` from
`CREDS`, auto layout, canonical key order, `pinData: {}`, `active: false`.

If a node you need has no helper, call `wf.add(name, type, typeVersion, params, cred=...)` directly and add a
helper afterwards so the next workflow benefits.

## 2. Node allowlist (type -> typeVersion we ship)

Use these versions. They all exist in n8n 2.37 (old versions are never removed) and their parameter shapes are
stable. Details and parameter shapes: `reference/nodes.md`.

| Purpose | type | version |
|---|---|---|
| Webhook / Respond | `n8n-nodes-base.webhook` / `respondToWebhook` | 2 / 1.1 |
| Schedule | `n8n-nodes-base.scheduleTrigger` | 1.2 |
| Form | `n8n-nodes-base.formTrigger` | 2.2 |
| IMAP / SMTP | `n8n-nodes-base.emailReadImap` / `emailSend` | 2 / 2.1 |
| Local file trigger / Read-Write file | `localFileTrigger` / `readWriteFile` | 1 / 1 |
| Telegram trigger / send | `telegramTrigger` / `telegram` | 1.1 / 1.2 |
| Postgres | `n8n-nodes-base.postgres` | 2.5 |
| Redis | `n8n-nodes-base.redis` | 1 |
| S3 (MinIO) | `n8n-nodes-base.s3` | 1 |
| HTTP Request | `n8n-nodes-base.httpRequest` | 4.2 |
| Code (JS only) | `n8n-nodes-base.code` | 2 |
| Edit Fields (Set) | `n8n-nodes-base.set` | 3.4 |
| If / Filter / Switch | `if` / `filter` / `switch` | 2.2 / 2.2 / 3.2 |
| Merge / Loop / Aggregate / Split Out / Sort / Limit | `merge` / `splitInBatches` / `aggregate` / `splitOut` / `sort` / `limit` | 3 / 3 / 1 / 1 / 1 / 1 |
| Remove Duplicates (in-run / across runs) | `removeDuplicates` | 1.1 / 2 |
| Extract / Convert file | `extractFromFile` / `convertToFile` | 1 / 1.1 |
| Compression / Crypto / HTML / RSS / Markdown / XML | 1.1 / 1 / 1.2 / 1.1 / 1 / 1 |
| Execute Workflow / sub-workflow trigger | `executeWorkflow` / `executeWorkflowTrigger` | 1.1 / 1.1 |
| Error Trigger / Stop and Error / Wait / NoOp | 1 / 1 / 1.1 / 1 |
| n8n API node | `n8n-nodes-base.n8n` | 1 |
| Chat Trigger | `@n8n/n8n-nodes-langchain.chatTrigger` | 1.1 |
| Agent | `@n8n/n8n-nodes-langchain.agent` | 2 |
| Ollama chat / embeddings | `lmChatOllama` / `embeddingsOllama` | 1 / 1 |
| Qdrant vector store | `vectorStoreQdrant` | 1 |
| Data loader / text splitter | `documentDefaultDataLoader` / `textSplitterRecursiveCharacterTextSplitter` | 1 / 1 |
| LLM chain / Retrieval QA / Summarize | `chainLlm` / `chainRetrievalQa` / `chainSummarization` | 1.4 / 1.4 / 2 |
| Structured output parser | `outputParserStructured` | 1.2 |
| Information Extractor / Text Classifier | `informationExtractor` / `textClassifier` | 1 / 1 |
| Tool: workflow / code | `toolWorkflow` / `toolCode` | 1.3 / 1.1 |
| Memory | `memoryBufferWindow` | 1.3 |

**Never use**: `executeCommand` (disabled by default in 2.x, and we keep it disabled), `function`/`functionItem`,
`spreadsheetFile`, `cron`, `interval`, `start` (all deprecated), Python Code nodes (JS only, universal).

When the stack is running, `python scripts/dev/node-types.py <type>` prints the live parameter schema for any
node from `GET /types/nodes.json` - use it whenever a parameter shape is uncertain.

## 3. Expressions and data access

* Expression strings start with `=`: `"={{ $json.email }}"`. Plain strings have no `=`.
* Webhook body: `$json.body.<field>`; headers: `$json.headers['x-hub-signature-256']`; query: `$json.query.page`.
* Other nodes: `$('Node Name').item.json.field` (paired item) or `$('Node Name').first().json`.
* Luxon: `$now.toFormat('yyyy-LL-dd')`, `$now.minus({days: 7}).toISO()`; `$today`.
* Workflow context: `$workflow.id`, `$workflow.name`, `$execution.id`, `$execution.resumeUrl`, `$runIndex`.
* Do not use `$env.*` in shipped workflows (works locally but hides config); hostnames are compose service names.
* Code node (JS): `return $input.all().map(i => ({ json: {...i.json} }))`; per-item mode returns one `{json}`.
  Binary: `$input.first().binary.data`; build binary with `await this.helpers.prepareBinaryData(Buffer, name, mime)`.

## 4. Connections

* `main` lanes are indexed: If -> `out=0` true, `out=1` false. Switch -> rule order, fallback last.
  Loop Over Items -> `out=0` done, `out=1` loop body (which connects back into the loop node).
  `onError: continueErrorOutput` adds an error lane at the last output index.
* AI sub-nodes connect via `wf.attach(sub, parent, kind)` with kind in `ai_languageModel`, `ai_memory`, `ai_tool`,
  `ai_outputParser`, `ai_embedding`, `ai_document`, `ai_textSplitter`, `ai_vectorStore`, `ai_retriever`.

## 5. Credentials and services

Reference credentials only through `CREDS` keys (`postgres`, `redis`, `smtp`, `imap`, `s3`, `ollama`, `qdrant`,
`n8n`, `telegram`, `github`, `header`). `scripts/bootstrap.py` creates exactly these ids/names from `.env`, so imported
workflows are wired without clicking. Service hostnames inside the stack: `postgres`, `redis`, `mailpit:1025`,
`greenmail:3143`, `minio:9000`, `mock-api:8080`, `docgen:8090`, `ollama:11434`, `qdrant:6333`, `whisper:9000`.
Files: `/home/node/.n8n-files/seed/...` (read-only seed files), `/home/node/.n8n-files/data/...` (writable, = repo `data/`).

## 6. Production judgement (what reviewers look for)

* Every workflow sets `error_workflow=catalog_id("P01")` unless it *is* P01. Folder slugs and ids of every catalog item come from `CATALOG` / `catalog_id(code)` in the builder; sub-workflow calls use `execute_workflow(wf, name, catalog_id("P03"))`.
* External calls (`http`, `postgres_*`, `s3_*`) get `.retry(3, 1000)` unless retrying is unsafe (non-idempotent writes).
* Webhooks respond with explicit codes (400 on bad schema, 202 on accepted-async, 409 on duplicate).
* Idempotency for anything replayable: Redis `INCR` guard (P03) or `ON CONFLICT` upserts.
* Batch and rate-limit outbound loops (`loop(batch_size)`, `http(batching=(10, 1000))`).
* A sticky note at the top-left explains the flow in two or three lines and names the patterns used.

## 7. Verification loop (do all of it before calling a workflow shipped)

1. `python .claude/skills/n8n-workflow-json/authoring/<script>.py` regenerates JSON.
2. `python scripts/validate.py workflows/<folder>` passes (contract, secrets, structure).
3. `python scripts/render-preview.py workflows/<folder>` refreshes `assets/screenshot.png` (or capture a real canvas
   screenshot with `/screenshot <ID>` when the stack is running).
4. With the stack up: `bash scripts/import-workflows.sh workflows/<folder>` then exercise the trigger (curl the
   webhook / run `scripts/dev/run-workflow.py <ID>`) and check the execution succeeded (`scripts/dev/executions.py`).
5. Update the README front-matter `status: shipped` only after step 4 succeeded.
