# PRD — Automation Lab

**Repo:** `automation-lab` (public, GitHub)
**Owner:** Youssef Almerash
**Status:** Draft v1
**Last updated:** 2026-09-06

> Implementation deviations from this document are recorded as ADRs in `docs/decisions/`.

---

## 1. Summary

A public, self-contained repository that demonstrates the full range of workflow automation. A visitor clones the repo, runs `docker compose up`, and gets a working n8n instance pre-wired to Postgres, Redis, Ollama, Qdrant, MinIO and Mailpit. Every workflow in the repo imports into that instance and runs immediately against seeded local data — no cloud accounts, no API keys, no paid tiers.

The repo is organised as a **coverage matrix**, not a folder of loose JSON files. Each workflow is a self-contained folder with its own README, screenshot and exported definition. A dedicated `patterns/` section covers production concerns (error handling, retries, idempotency, rate limiting) that most automation tutorials skip.

---

## 2. Goals

| # | Goal | Measure |
|---|------|---------|
| G1 | Demonstrate breadth of automation capability | ≥ 8 categories covered, ≥ 25 workflows shipped |
| G2 | Zero-friction reproduction | Clone → first workflow running in < 10 minutes on a clean machine |
| G3 | Run fully offline / free | No workflow requires a paid service or a credit card to execute |
| G4 | Show production judgement, not just happy paths | `patterns/` folder with ≥ 6 documented patterns, each referenced by ≥ 2 workflows |
| G5 | Be legible in 60 seconds | Root README coverage matrix + screenshot per workflow |
| G6 | Stay maintainable | CI validates every workflow JSON on every push |

## 3. Non-goals

- Not a hosted SaaS or a product with users.
- Not an n8n fork, custom node library, or tutorial series.
- Not a "1000 workflows" dump. Quality and documentation over count.
- No workflow that requires a paid API key to run its core path. (Optional paid variants may be documented, never required.)
- No production data, real client data, or employer material of any kind.

---

## 4. Audience

| Persona | Need | What they do in the repo |
|---------|------|--------------------------|
| **Recruiter / hiring manager** | Assess skill in 2 minutes | Reads root README, scans coverage matrix, opens 1–2 screenshots |
| **Engineer evaluating n8n** | See real patterns before committing | Reads `patterns/`, `docs/tool-comparison.md` |
| **Self-hoster / learner** | Working examples they can run | Clones, runs compose, imports a workflow |
| **Future me** | Reusable building blocks | Copies a pattern into a client project |

---

## 5. Success metrics

| Metric | Target (90 days) |
|--------|------------------|
| Time from clone to first successful execution | < 10 min |
| Workflows shipped and documented | ≥ 25 |
| Workflows that run with zero external accounts | 100% of core path |
| CI passing on `main` | Always |
| GitHub stars | ≥ 50 (secondary, not a driver) |
| Broken/unimportable workflow reports | 0 open |

---

## 6. Architecture

### 6.1 Services (Docker Compose)

Compose **profiles** keep the base stack light. Not every machine can run Ollama.

| Service | Profile | Port | Purpose |
|---------|---------|------|---------|
| `n8n` | core | 5678 | Workflow engine (Community Edition) |
| `postgres` | core | 5432 | n8n persistence + `demo` database for workflow data |
| `redis` | core | 6379 | Queue mode, dedupe/idempotency store |
| `mailpit` | core | 8025 / 1025 | Local SMTP + web inbox for email workflows |
| `minio` | core | 9000 / 9001 | S3-compatible object storage for backups/artifacts |
| `ollama` | ai | 11434 | Local LLM (default model: `llama3.2:3b`) |
| `qdrant` | ai | 6333 | Vector store for RAG |
| `metabase` | observability | 3001 | Dashboards over execution logs |
| `mock-api` | core | 8080 | Static JSON API (json-server) for polling/pagination demos |

**Commands**

```bash
docker compose --profile core up -d              # base stack
docker compose --profile core --profile ai up -d # + local AI
```

### 6.2 Hardware baseline

- **core profile:** 2 vCPU, 4 GB RAM, 5 GB disk
- **ai profile:** 4 vCPU, 8 GB RAM, 15 GB disk (model download ~2 GB)

Documented in README so nobody starts a download they can't finish.

### 6.3 Data seeding

`seed/` contains:
- `schema.sql` — demo tables (customers, orders, products, tickets, attendance)
- `seed.sql` — ~500 synthetic rows, fully fictional
- `files/` — sample CSV, XLSX, PDF, receipt image, audio clip
- `payloads/` — sample webhook JSON bodies for testing

Run automatically by a Postgres init script on first boot.

---

## 7. Repository structure

```
automation-lab/
├─ README.md                    # index, coverage matrix, quickstart
├─ LICENSE                      # MIT
├─ CONTRIBUTING.md
├─ .env.example
├─ docker/
│  ├─ docker-compose.yml
│  ├─ postgres/init/
│  └─ mock-api/db.json
├─ seed/
├─ workflows/
│  ├─ T01-webhook-to-database/
│  │  ├─ README.md
│  │  ├─ workflow.json
│  │  ├─ assets/screenshot.png
│  │  └─ test/payload.json
│  └─ …
├─ patterns/
│  ├─ P01-error-handler/
│  └─ …
├─ scripts/
│  ├─ export-workflows.sh       # pull from n8n → repo, strip credentials
│  ├─ import-workflows.sh       # push repo → n8n
│  ├─ validate.py               # JSON schema + secret scan
│  └─ build-matrix.py           # regenerate README coverage table
├─ docs/
│  ├─ tool-comparison.md        # n8n vs Activepieces vs Node-RED vs cron+Python
│  ├─ security.md
│  └─ decisions/                # short ADRs
└─ .github/workflows/
   ├─ validate.yml
   └─ matrix.yml
```

---

## 8. Workflow folder contract

Every folder under `workflows/` **must** contain:

1. `README.md` following the template in Appendix A
2. `workflow.json` — n8n export, pretty-printed, credentials stripped
3. `assets/screenshot.png` — canvas view, ≥ 1200px wide
4. `test/` — at least one sample input (payload, CSV row, or seed query)

**Naming:** `<CATEGORY><NN>-<kebab-case-name>` — e.g. `A02-ticket-classifier`.

**Acceptance criteria for "done":**
- [ ] Imports into a clean n8n instance with no manual node fixes
- [ ] Executes end-to-end against seeded data only
- [ ] README lists every credential needed, with setup steps
- [ ] Screenshot present and current
- [ ] Passes `scripts/validate.py`
- [ ] Row added to the coverage matrix
- [ ] References at least one pattern from `patterns/` where applicable

---

## 9. Scope — workflow catalog

### 9.1 Triggers (T) — every way work starts

| ID | Workflow | Notes |
|----|----------|-------|
| T01 | Webhook → validate → Postgres → respond | Includes 400 response on bad schema |
| T02 | Scheduled daily digest | Cron, timezone-aware |
| T03 | Polling an API without webhooks | Cursor stored in Redis, no duplicate reads |
| T04 | IMAP email trigger → parse attachment | Runs against Mailpit |
| T05 | n8n Form trigger → record + confirmation email | |
| T06 | File watcher → process on drop | Local folder mount |
| T07 | Telegram chat trigger → command router | Only workflow needing a free external token; documented alternative provided |

### 9.2 Data & ETL (D)

| ID | Workflow | Notes |
|----|----------|-------|
| D01 | CSV/XLSX → validate → Postgres | Row-level error report, not all-or-nothing |
| D02 | Web scrape → structured JSON | HTML extraction + pagination |
| D03 | Multi-source API aggregation → normalized dataset | 3 shapes → 1 schema |
| D04 | Incremental sync with upsert + dedupe | Change detection via hash |
| D05 | Scheduled DB dump → MinIO with rotation | Keeps last 7 |

### 9.3 Monitoring & alerts (M)

| ID | Workflow | Notes |
|----|----------|-------|
| M01 | Uptime monitor with escalation | Warn → alert → recovery notice |
| M02 | GitHub events → chat notification | |
| M03 | RSS → keyword-filtered digest | Dedupe across runs |
| M04 | DB threshold alert | Query result crosses limit → notify |
| M05 | Price / exchange-rate watcher | Stores history, alerts on % change |

### 9.4 Documents & reports (R)

| ID | Workflow | Notes |
|----|----------|-------|
| R01 | Data → PDF invoice | Template + line items |
| R02 | Bulk certificate generation from CSV | One PDF per row, zipped |
| R03 | Data → Arabic RTL DOCX/PPTX report | **Flagship.** Correct shaping/bidi, Western numerals |
| R04 | PDF → structured fields → dataset | Table extraction, the "no API access" case |

> R03 + R04 chained is the strongest single demo in the repo: a locked PDF export becomes structured rows becomes a formatted bilingual report.

### 9.5 AI (A) — all local, no API keys

| ID | Workflow | Notes |
|----|----------|-------|
| A01 | RAG chatbot over repo docs | Qdrant + Ollama |
| A02 | Ticket/email classification → routing | Structured JSON output, validated |
| A03 | Audio → transcript → summary → task list | Whisper local |
| A04 | Arabic OCR → structured data | |
| A05 | Agent with tools calling sub-workflows | Shows sub-workflow architecture |
| A06 | Long article → social variants | Content repurposing |

### 9.6 Business operations (B)

| ID | Workflow | Notes |
|----|----------|-------|
| B01 | Lead capture → enrich → CRM row → follow-up sequence | Sequence with delays + exit condition |
| B02 | Booking → calendar → reminder chain | |
| B03 | Receipt image → OCR → sheet → monthly rollup | |
| B04 | Support inbox triage → assign → SLA timer | Uses A02 |

### 9.7 DevOps & self-automation (O)

| ID | Workflow | Notes |
|----|----------|-------|
| O01 | GitHub Actions: validate JSON + lint on PR | |
| O02 | Actions: auto-changelog + release tagging | |
| O03 | Issue triage bot: label, assign, stale-close | |
| O04 | n8n → git: nightly workflow export back to repo | Workflow-as-code, closes the loop |
| O05 | Execution logs → Postgres → Metabase dashboard | Observability |

### 9.8 Patterns (P) — the differentiator

| ID | Pattern | Content |
|----|---------|---------|
| P01 | Global error handler workflow | Catches failures across all workflows, notifies with context |
| P02 | Retry with exponential backoff | Distinguishes retryable vs terminal errors |
| P03 | Idempotency | Redis key per external ID; safe replays |
| P04 | Rate limiting & batching | Chunked processing, respects API quotas |
| P05 | Sub-workflow modularity | Shared building blocks, versioning, contracts |
| P06 | Testing & mock payloads | How to test without hitting live systems |
| P07 | Secrets in a public repo | `.env` conventions, credential naming, what never gets committed |
| P08 | Observability | Structured logs, execution metrics, alert fatigue |

---

## 10. Documentation requirements

### Root README (in order)

1. One-line description + hero screenshot/GIF
2. Quickstart (4 commands max)
3. **Coverage matrix** — auto-generated by `scripts/build-matrix.py` from workflow front-matter
4. Patterns index
5. Stack table with links
6. Hardware requirements
7. License + credits

### `docs/tool-comparison.md`

Same workflow (webhook → transform → store → notify) implemented four ways:

| Tool | LOC / nodes | Setup time | Best for |
|------|-------------|------------|----------|
| n8n | | | |
| Activepieces | | | |
| Node-RED | | | |
| Python + GitHub Actions cron | | | |

Ends with an honest recommendation table, including where n8n is the wrong choice.

---

## 11. CI/CD

| Job | Trigger | Does |
|-----|---------|------|
| `validate` | PR + push | JSON parse, required-keys check, secret scan (regex for keys/tokens/emails), folder contract check |
| `matrix` | push to `main` | Regenerates coverage matrix, commits if changed |
| `links` | weekly | Dead link check in all READMEs |
| `compose-smoke` | PR touching `docker/` | `docker compose config` + core profile boot test |

**Secret scan is blocking.** Any commit containing something that looks like a credential fails the build.

---

## 12. Security & licensing

- Repo license: **MIT** (covers your workflows and scripts).
- n8n itself is fair-code (Sustainable Use License). Exported workflow JSON is your own work; the repo doesn't redistribute n8n source. State this in the README to avoid confusion.
- `.env` is git-ignored; `.env.example` documents every variable with dummy values.
- n8n exports reference credentials by name, not value — but `scripts/export-workflows.sh` strips credential blocks anyway.
- All seed data is synthetic. No employer, client, or personal data enters the repo. No exception.
- `docs/security.md` explains threat model for anyone self-hosting the stack (default ports, basic auth, don't expose 5678 publicly).

---

## 13. Milestones

| Phase | Deliverable | Exit criteria |
|-------|-------------|---------------|
| **M0 — Foundation** | Compose stack, repo skeleton, seed data, CI, README with full matrix (mostly unchecked) | `docker compose --profile core up` works on a clean machine; CI green |
| **M1 — Core loop** | T01, T02, T03, D01, P01, P02 | Error handling proven; first screenshots in |
| **M2 — Documents** | R01–R04 | R03/R04 chain demoed end-to-end with a GIF |
| **M3 — AI** | A01–A03, ai profile, P05 | RAG chatbot runs offline on 8 GB RAM |
| **M4 — Ops & business** | B01–B04, O01–O05, P03, P04 | O04 self-export loop running nightly |
| **M5 — Polish & launch** | tool-comparison.md, hero GIF, remaining patterns, launch post | 25+ workflows, all acceptance criteria met |

Ship M0 and M1 before announcing anything publicly. An empty repo with a big matrix reads as abandoned.

---

## 14. Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Scope creep → 40 half-done workflows | Repo looks abandoned | Hard "done" definition; matrix shows planned vs shipped explicitly |
| Ollama too heavy for visitors' machines | Half the repo unrunnable | `ai` profile is opt-in; every AI workflow documents a smaller model fallback |
| Accidental credential commit | Serious, public | Blocking secret scan + pre-commit hook |
| n8n version drift breaks old exports | Silent rot | Pin n8n image tag; CI import test; note tested version in each README |
| Workflow JSON is unreadable in diffs | Reviewers can't follow changes | Pretty-printed exports; changes described in PR body |
| Arabic rendering breaks in R03 | Flagship demo fails | Font bundled in repo; visual regression screenshot in test/ |

---

## 15. Future (v2, out of scope now)

- Custom n8n community node published to npm
- One-click deploy button (Railway / Coolify)
- Video walkthrough per category
- Bilingual README (Arabic + English)
- Workflow generator: describe a workflow in natural language → scaffold JSON

---

## Appendix A — Workflow README template

```markdown
# T01 — Webhook to Database

**Category:** Triggers · **Difficulty:** Beginner · **Tested on:** n8n 1.x
**Patterns used:** P01 (error handler), P03 (idempotency)

## Problem
One paragraph. What real situation does this solve?

## How it works
1. Step
2. Step
3. Step

![screenshot](assets/screenshot.png)

## Setup
- Services needed: `core` profile
- Credentials: `Postgres — demo` (see .env.example)
- Import: `./scripts/import-workflows.sh workflows/T01-webhook-to-database`

## Try it
curl -X POST http://localhost:5678/webhook/t01 \
  -H 'Content-Type: application/json' \
  -d @test/payload.json

## Notes & trade-offs
What you'd change for production. What this deliberately doesn't handle.
```

---

## Appendix B — Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `N8N_HOST` | `localhost` | |
| `N8N_PORT` | `5678` | |
| `N8N_BASIC_AUTH_ACTIVE` | `true` | |
| `N8N_ENCRYPTION_KEY` | *(generated)* | Must be set before first boot |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `n8n` | |
| `DEMO_DB` | `demo` | Workflow data, separate from n8n's own DB |
| `REDIS_HOST` | `redis` | |
| `OLLAMA_MODEL` | `llama3.2:3b` | Override for weaker/stronger machines |
| `QDRANT_URL` | `http://qdrant:6333` | |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | `minioadmin` | Local only — warned in docs |
| `SMTP_HOST` / `SMTP_PORT` | `mailpit` / `1025` | |
| `GENERIC_TIMEZONE` | `Africa/Tripoli` | |

---

## Appendix C — Coverage matrix format

Auto-generated. Each workflow README carries YAML front-matter:

```yaml
---
id: T01
title: Webhook to Database
category: Triggers
difficulty: Beginner
status: shipped   # planned | in-progress | shipped
patterns: [P01, P03]
services: [core]
---
```

`scripts/build-matrix.py` reads every front-matter block and rewrites the table between `<!-- MATRIX:START -->` and `<!-- MATRIX:END -->` in the root README.
