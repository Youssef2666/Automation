# A01 test inputs

The "input" of A01 is a question. `chat-request.json` is one ready-to-post chat message; `questions.json` is the
small evaluation set - three questions the docs can answer, two they cannot, one empty message, and one that the
current setup gets **wrong** on purpose (see the last entry). Each carries the answer observed on 2026-09-07 so a
prompt or model change has something to diff against.

The corpus is the repository's own markdown, so there is no fixture to load: index it once, then ask.

```bash
# 1. index (or re-index) the corpus - ~60 s from empty, ~1.5 s when nothing changed
python scripts/dev/run-workflow.py A01

# 2. ask one question (the chat webhook id is stable: it is derived from the workflow id)
curl -s -X POST http://localhost:5678/webhook/f5387d42-2ec6-5c40-9ab6-872129623d74/chat \
  -H 'Content-Type: application/json' \
  -d @workflows/A01-rag-docs-chatbot/test/chat-request.json | python -m json.tool

# 3. or walk the whole set
python - <<'PY'
import json, time, urllib.request
URL = "http://localhost:5678/webhook/f5387d42-2ec6-5c40-9ab6-872129623d74/chat"
spec = json.load(open("workflows/A01-rag-docs-chatbot/test/questions.json", encoding="utf-8"))
for q in spec["questions"]:
    body = json.dumps({"action": "sendMessage", "sessionId": "eval", "chatInput": q["chatInput"]}).encode()
    t0 = time.time()
    with urllib.request.urlopen(urllib.request.Request(
            URL, data=body, method="POST", headers={"Content-Type": "application/json"}), timeout=900) as r:
        d = json.load(r)
    ok = d["reason"] == q["expect"]["reason"] and d["grounded"] == q["expect"]["grounded"]
    print(f'{"PASS" if ok else "FAIL"}  {q["id"]:32s} {time.time()-t0:5.1f}s  reason={d["reason"]}')
    print("   " + d["output"].replace("\n", "\n   "))
PY
```

Every question also leaves one `execution_log` row (through P08):

```bash
docker compose exec -T postgres psql -U n8n -d demo -c \
  "select execution_id, status, duration_ms, left(error_message, 90) as notes
     from execution_log where workflow_id = 'ALA01RagDocsChat' order by id desc limit 8"
```

`status` is `success` for a grounded answer, `warning` for an honest refusal (`not_in_docs`, `below_threshold`,
`not_asked`) and `error` for a failure the chat absorbed (`model_error`, `index_error`).

## Forcing the failure lanes (no waiting for bad luck)

**Retrieval failure** - stop Qdrant for one question:

```bash
docker compose stop qdrant
curl -s -X POST http://localhost:5678/webhook/f5387d42-2ec6-5c40-9ab6-872129623d74/chat \
  -H 'Content-Type: application/json' -d '{"action":"sendMessage","sessionId":"x","chatInput":"What is P08 for?"}'
docker compose start qdrant
```

Expected: `reason: index_error`, "I could not reach the docs index...", with `(getaddrinfo EAI_AGAIN qdrant)`
appended - and an `error` row in `execution_log`. The chat answers; it does not 500.

**Model failure** - open **A01 - RAG Chatbot over the Repo Docs** in n8n, set the **Ollama (llama3.2:3b)** node's
model to `no-such-model:1b`, click **Publish** (n8n 2.x runs the *published* version, so an unpublished edit
changes nothing), then ask anything.

Expected: `reason: model_error`, "The local model did not return an answer...", the four passages it *did*
retrieve, and `(model 'no-such-model:1b' not found)`. Restore with
`bash scripts/import-workflows.sh workflows/A01-rag-docs-chatbot --publish`.

**Empty index** - `curl -s -X DELETE localhost:6333/collections/lab_docs`, then ask: `reason: empty_index`,
"The docs index is empty. Run the ingest path of A01 once...". `python scripts/dev/run-workflow.py A01` rebuilds it.

## Proving the incremental re-index

Nothing in the repo has to change. Make the index *look* stale and re-run:

```bash
# pretend docs/security.md was edited: its chunks now carry a hash that does not match the file
curl -s -X POST 'localhost:6333/collections/lab_docs/points/payload?wait=true' -H 'Content-Type: application/json' \
  -d '{"payload":{"metadata":{"source":"docs/security.md","content_hash":"stale-on-purpose"}},
       "filter":{"must":[{"key":"metadata.source","match":{"value":"docs/security.md"}}]}}'
python scripts/dev/run-workflow.py A01
curl -s localhost:6333/collections/lab_docs | python -m json.tool | grep points_count
```

Expected notes on the `execution_log` row: `embedded 1 file(s) (0 new, 1 changed) = N chunk(s), skipped 21
unchanged; dropped the chunks of 1 file(s)` - and `points_count` back at the same total, not higher.
Delete a file's chunks instead (`points/delete` with the same filter) and it comes back as `1 new`.
