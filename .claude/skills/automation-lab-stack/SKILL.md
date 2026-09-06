---
name: automation-lab-stack
description: The local Docker Compose stack - services, profiles, ports, in-container hostnames, canonical credential ids/names, volume mounts, mock-api and docgen endpoints, bootstrap/import/export scripts and how to verify a running stack. Load when touching docker/, scripts/, seed/, or when a workflow needs to talk to a service.
---

# Automation Lab stack (docker/docker-compose.yml)

Compose file: `docker/docker-compose.yml` (project name `automation-lab`), env from repo-root `.env`
(copy of `.env.example`). Run from the repo root; `.claude/settings.json` sets `COMPOSE_FILE` for you.

## Profiles and services

| Service | Profile | Host port | In-stack address | Image (pinned) | Purpose |
|---|---|---|---|---|---|
| n8n | core | 5678 | `http://n8n:5678` | `n8nio/n8n:2.37.10` | workflow engine |
| postgres | core | 5432 | `postgres:5432` | `postgres:17-alpine` | dbs `n8n` (engine) + `demo` (workflow data) |
| redis | core | 6379 | `redis:6379` | `redis:7-alpine` | cursors, idempotency keys, optional queue mode |
| mailpit | core | 8025 UI / 1025 SMTP | `mailpit:1025` | `axllent/mailpit:v1.31.1` | outbound mail sink + inbox UI |
| greenmail | core | 3025 SMTP / 3143 IMAP | `greenmail:3143` | `greenmail/standalone:2.1.13` | IMAP inbox for T04/B04 (Mailpit has no IMAP) |
| minio | core | 9000 API / 9001 console | `http://minio:9000` | `minio/minio:RELEASE.2025-09-07T16-13-09Z` | S3 buckets `backups`, `artifacts` |
| minio-init | core | - | - | `minio/mc` | one-shot bucket creation |
| mock-api | core | 8080 | `http://mock-api:8080` | built from `docker/mock-api` (json-server 0.17) | polling, pagination, flaky/rate-limited endpoints, RSS, HTML catalog |
| docgen | docs | 8090 | `http://docgen:8090` | built from `docker/docgen` (FastAPI) | HTML->PDF, DOCX/PPTX (Arabic RTL), PDF tables, OCR (eng+ara) |
| ollama | ai | 11434 | `http://ollama:11434` | `ollama/ollama:0.33.3` | local LLM (`OLLAMA_MODEL`, default `llama3.2:3b`) + `nomic-embed-text` |
| ollama-pull | ai | - | - | same | one-shot model download |
| qdrant | ai | 6333 | `http://qdrant:6333` | `qdrant/qdrant:v1.19.1` | vector store |
| whisper | ai | 9010 | `http://whisper:9000` | `onerahmet/openai-whisper-asr-webservice:v1.10.0` | speech-to-text (`POST /asr`) |
| metabase | observability | 3001 | `http://metabase:3000` | `metabase/metabase:v0.63.16.6` | dashboards over `demo.execution_log` |
| n8n-worker | queue | - | - | n8n image, `worker` command | optional queue mode (`EXECUTIONS_MODE=queue`) |

Commands:

```bash
docker compose --profile core up -d                       # base
docker compose --profile core --profile docs up -d        # + document generation
docker compose --profile core --profile ai up -d          # + local AI (needs ~8 GB RAM)
docker compose --profile core --profile observability up -d
docker compose ps ; docker compose logs -f n8n
```

Hardware baseline: core 2 vCPU / 4 GB / 5 GB disk; ai 4 vCPU / 8 GB / 15 GB (model download ~2.3 GB).

## n8n configuration decisions (see docs/decisions/)

* `NODES_EXCLUDE=["n8n-nodes-base.executeCommand"]` - keeps Execute Command disabled, re-enables Local File Trigger.
* File access uses the 2.x default `N8N_RESTRICT_FILE_ACCESS_TO=/home/node/.n8n-files`; we mount
  `./seed/files -> /home/node/.n8n-files/seed` (ro), `./data -> /home/node/.n8n-files/data` (rw),
  `./docs -> /home/node/.n8n-files/docs` (ro), `./patterns -> /home/node/.n8n-files/patterns` (ro),
  `./workflows -> /repo/workflows` (ro, for CLI import), `./patterns -> /repo/patterns` (ro).
* `N8N_SECURE_COOKIE=false` (plain http on localhost), `N8N_DIAGNOSTICS_ENABLED=false`, `GENERIC_TIMEZONE=Africa/Tripoli`.
* Owner account: n8n 2.x has no basic auth; `scripts/bootstrap.py` creates the owner from `N8N_OWNER_EMAIL/PASSWORD`
  (or you do it in the UI on first visit).
* Workflows are imported **unpublished**; `scripts/setup.sh` publishes those with `autopublish: true`.

## Canonical credentials (created by scripts/bootstrap.py, referenced by id in every workflow)

| key | type | id | name | values from |
|---|---|---|---|---|
| postgres | postgres | `ALcredPostgresDm` | Postgres - demo | `POSTGRES_USER/PASSWORD`, db `DEMO_DB` |
| redis | redis | `ALcredRedisLocal` | Redis - local | host redis |
| smtp | smtp | `ALcredSmtpMailpt` | SMTP - Mailpit | mailpit:1025, no auth, no TLS |
| imap | imap | `ALcredImapGreenM` | IMAP - GreenMail | greenmail:3143, login `inbox` / `inbox` (mailbox inbox@lab.local) |
| s3 | s3 | `ALcredS3MinioLoc` | S3 - MinIO | `MINIO_ROOT_USER/PASSWORD`, endpoint http://minio:9000, path-style |
| ollama | ollamaApi | `ALcredOllamaLocl` | Ollama - local | http://ollama:11434 |
| qdrant | qdrantApi | `ALcredQdrantLocl` | Qdrant - local | http://qdrant:6333 |
| n8n | n8nApi | `ALcredN8nApiLocl` | n8n API - local | API key minted by bootstrap |
| header | httpHeaderAuth | `ALcredWebhookHdr` | Webhook - header auth | `X-Lab-Key: LAB_WEBHOOK_KEY` |
| telegram | telegramApi | `ALcredTelegramBt` | Telegram - bot | `TELEGRAM_BOT_TOKEN` (optional) |
| github | githubApi | `ALcredGithubTokn` | GitHub - token | `GITHUB_TOKEN` (optional) |

## mock-api endpoints (json-server + middleware, `docker/mock-api/`)

* REST collections with `_page/_limit/_sort/_order`, `id` filters: `/products`, `/customers`, `/orders`, `/events`, `/companies`, `/posts`
* `/events?id_gte=<cursor>&_sort=id&_limit=50` - append-only feed for cursor polling (T03)
* `/flaky` - ~50% `503` + `Retry-After` (P02); `/ratelimited` - 429 above 5 req / 10 s (P04); `/slow?ms=3000`
* `/feed.xml` - RSS built from `/posts` (M03); `/catalog?page=1..3` - paginated HTML product cards (D02)
* `/rates/latest?base=USD` - jittered FX rates (M05); `/health` 200, `/health/down` 503 (M01)
* `/companies/lookup?domain=acme.example.com` - enrichment (B01)
* `/webhooks/sink` - accepts POSTs and echoes (for notification targets)

## docgen endpoints (`docker/docgen/`, FastAPI, profile docs)

* `POST /render/pdf` `{html, filename}` -> PDF bytes (WeasyPrint, Amiri font for Arabic)
* `POST /render/docx` and `POST /render/pptx` `{title, lang, rtl, sections:[{heading, paragraphs[], table:{columns[], rows[][]}}]}` -> file
* `POST /pdf/extract` multipart `file` -> `{text, tables:[{page, rows[][]}]}` (pdfplumber)
* `POST /ocr?lang=ara+eng` multipart `file` -> `{text, lines[], mean_confidence}` (Tesseract)
* `GET /health`

## Scripts

| script | does |
|---|---|
| `scripts/setup.sh` | wait for n8n, bootstrap owner + API key + credentials, import all workflows, publish `autopublish` ones |
| `scripts/bootstrap.py` | the Python behind setup (internal REST for owner/API key, CLI import for credentials) |
| `scripts/import-workflows.sh [folder...]` | `n8n import:workflow` per folder (all when no args), `--publish` to activate |
| `scripts/export-workflows.sh` | `n8n export:workflow --backup`, strip credentials/pinData/instance meta, write back to folders by id |
| `scripts/validate.py [paths] [--json] [--quiet]` | folder contract + JSON structure + secret scan; CI gate |
| `scripts/build-matrix.py [--check]` | regenerate the README coverage matrix from front-matter |
| `scripts/render-preview.py [folder...]` | draw `assets/screenshot.png` from workflow.json (Pillow) |
| `scripts/dev/*.py` | live-stack helpers: node-types, run-workflow, executions, smoke test |

## Verifying a running stack

```bash
curl -s http://localhost:5678/healthz            # {"status":"ok"}
curl -s http://localhost:8080/health             # mock-api
curl -s http://localhost:8025/api/v1/messages | head -c 300   # Mailpit inbox
docker compose exec -T postgres psql -U n8n -d demo -c 'select count(*) from customers'
```
