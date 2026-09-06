---
id: __ID__
title: __TITLE__
category: __CATEGORY__
difficulty: __DIFFICULTY__
status: in-progress
patterns: []
services: [core]
tested_on: n8n 2.37.10
---

# __ID__ - __TITLE__

**Category:** __CATEGORY__ · **Difficulty:** __DIFFICULTY__ · **Tested on:** n8n 2.37.10
**Patterns used:** _none yet_

## Problem

One paragraph. What real situation does this solve, and what goes wrong when people do it by hand?

## How it works

1. Trigger: ...
2. Validate / transform: ...
3. Store / notify: ...

![screenshot](assets/screenshot.png)

## Setup

- Services needed: `core` profile (`docker compose --profile core up -d`)
- Credentials: `Postgres - demo` (created by `scripts/setup.sh` from `.env`)
- Import: `bash scripts/import-workflows.sh workflows/__FOLDER__`
- Activate: published automatically by setup when `autopublish: true`; otherwise open the workflow and click **Publish**.

## Try it

```bash
curl -X POST http://localhost:5678/webhook/__PATH__ \
  -H 'Content-Type: application/json' \
  -d @workflows/__FOLDER__/test/payload.json
```

Then check: ...

## Notes & trade-offs

- What you would change for production: ...
- What this deliberately does not handle: ...
