---
name: workflow-folder-contract
description: The folder contract every workflows/<ID>-<slug> and patterns/<ID>-<slug> folder must satisfy (README front-matter + sections, workflow.json, assets/screenshot.png, test/), the naming scheme, the acceptance checklist for "shipped", and the step-by-step authoring procedure. Load when creating, reviewing, or auditing a workflow or pattern folder.
---

# Workflow folder contract

## Layout

```
workflows/T01-webhook-to-database/
├─ README.md            # front-matter + template sections (templates/README.md)
├─ workflow.json        # n8n export, pretty-printed, credentials by {id,name} only, pinData {}
├─ assets/
│  └─ screenshot.png    # canvas view, >= 1200px wide (auto-rendered preview until a real capture exists)
└─ test/
   ├─ payload.json      # or *.csv, *.sql, *.eml, *.wav ... at least one sample input
   └─ README.md         # optional: how to replay the sample(s)
```

Naming: `<CATEGORY><NN>-<kebab-case-name>` where CATEGORY in `T D M R A B O P` (P = patterns, lives in `patterns/`).
The README `id` must equal the folder prefix (`T01`). One workflow per folder; sub-workflows a workflow depends on
live in `patterns/` (P05 building blocks) or in their own folder and are listed under `depends_on`.

## Front-matter (machine-read by scripts/build-matrix.py and scripts/validate.py)

```yaml
---
id: T01
title: Webhook to Database
category: Triggers            # Triggers | Data & ETL | Monitoring | Documents | AI | Business | DevOps | Patterns
difficulty: Beginner          # Beginner | Intermediate | Advanced
status: shipped               # planned | in-progress | shipped
patterns: [P01, P03]          # pattern ids referenced (patterns folders use [])
services: [core]              # compose profiles required: core | docs | ai | observability
tested_on: n8n 2.37.10
autopublish: true             # optional: scripts/setup.sh publishes it so its trigger is live
depends_on: [P03]             # optional: workflows that must be imported first (sub-workflows)
external: none                # optional: e.g. "Telegram bot token (free)" - core path must never need it
---
```

## README sections (in this order - see templates/README.md)

1. `# T01 - Webhook to Database` then the meta line: **Category** - **Difficulty** - **Tested on** - **Patterns used**
2. `## Problem` - one paragraph, the real situation.
3. `## How it works` - numbered steps that match the canvas left-to-right, then `![screenshot](assets/screenshot.png)`.
4. `## Setup` - services/profiles, credentials (by name), import command, activation note.
5. `## Try it` - copy-paste commands using `test/` files; what to look at afterwards (Mailpit, table, MinIO...).
6. `## Notes & trade-offs` - what you would change for production, what this deliberately does not handle.

Patterns use `templates/pattern-README.md` (Problem, Pattern, Implementation, Trade-offs, Used by).

## Acceptance checklist for `status: shipped`

- [ ] `workflow.json` imports into a clean n8n 2.37 instance with no manual node fixes (`bash scripts/import-workflows.sh <folder>`)
- [ ] Executes end-to-end against seeded data only (documented in Try it; evidence: execution succeeded)
- [ ] README lists every credential needed (names match `CREDS` in the builder) and the setup steps
- [ ] `assets/screenshot.png` present, >= 1200px wide, matches the current canvas
- [ ] `python scripts/validate.py <folder>` passes (contract + secret scan)
- [ ] Row appears in the root README coverage matrix (`python scripts/build-matrix.py`)
- [ ] References at least one pattern from `patterns/` where applicable, and that pattern's README lists it under "Used by"
- [ ] No paid service or real account on the core path; optional paid/external variants are clearly marked optional

## Authoring procedure (workflow-builder agent follows this)

1. Read the catalog entry in `docs/PRD.md` (section 9) and the pattern READMEs you will reference.
2. Scaffold with `/new-workflow <ID> <slug> "<Title>"` (creates folder, README from template, authoring script stub, test/).
3. Write the authoring script (`.claude/skills/n8n-workflow-json/authoring/<ID>_<slug>.py`) and run it.
4. Write `test/` inputs (JSON payloads, CSV rows, SQL, .eml, audio) - synthetic data only, `@lab.local` emails.
5. Write the README (all sections, concrete commands, no placeholders left).
6. `python scripts/dev/canvas-screenshots.py <folder>` for a real canvas capture (stack up, headless Chromium via
   Playwright); `python scripts/render-preview.py <folder>` draws the auto-rendered preview when the stack is down.
7. `python scripts/validate.py <folder>`; fix everything it reports.
8. With the stack running: import, execute, verify (see n8n-workflow-json skill section 7). Then set `status: shipped`.
9. `python scripts/build-matrix.py` and update the pattern READMEs "Used by" lists.
