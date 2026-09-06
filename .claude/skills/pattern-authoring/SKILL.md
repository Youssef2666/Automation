---
name: pattern-authoring
description: How to write a patterns/Pxx folder - the production concern it addresses, the reusable sub-workflow it ships, how workflows adopt it, and how "referenced by >= 2 workflows" is tracked. Load when creating or updating anything under patterns/.
---

# Pattern authoring (patterns/P01..P08)

Patterns are the differentiator of this repo: production concerns most tutorials skip. Each pattern is a folder with the
same contract as a workflow (README front-matter, `workflow.json` when it ships a reusable building block,
`assets/screenshot.png`, `test/`), category `Patterns`.

| ID | Pattern | Ships | Adopted by (minimum) |
|---|---|---|---|
| P01 | Global error handler | Error Trigger workflow -> `execution_log` + email/Telegram with context | every workflow via `settings.errorWorkflow` |
| P02 | Retry with exponential backoff | sub-workflow `P02 - HTTP with backoff` (retryable vs terminal classification) | T03, D03, M05, B01 |
| P03 | Idempotency | sub-workflow `P03 - Idempotency guard` (Redis INCR per external id, TTL) | T01, M02, B01, D04 |
| P04 | Rate limiting & batching | sub-workflow `P04 - Throttled batch` + guidance on `batching` / Loop Over Items | D02, D03, R02, A06 |
| P05 | Sub-workflow modularity | contract doc + three building blocks (lookup customer, create ticket, log notification) | A05, B04, P01 |
| P06 | Testing & mock payloads | harness workflow that replays `test/` payloads against webhooks and asserts | T01, T05, M02, B01 |
| P07 | Secrets in a public repo | docs only (+ pre-commit hook and CI scan) | all |
| P08 | Observability | sub-workflow `P08 - Log execution` + structured log conventions + Metabase notes | O05, M01, P01 |

## README structure (templates/pattern-README.md in workflow-folder-contract)

1. **Problem** - concrete failure story (duplicate charges on replay, alert storms, silent 429s...).
2. **Pattern** - the rule in one paragraph; decision table for when it applies and when it does not.
3. **Implementation in n8n** - the shipped sub-workflow node by node; the exact way a caller wires it
   (Execute Workflow node with `catalog_id("P03")`, what to pass, what comes back).
4. **Trade-offs** - what it costs, what it does not solve.
5. **Used by** - list of workflow ids/titles. Keep this in sync with the workflows' `patterns:` front-matter
   (`python scripts/build-matrix.py` prints a warning when they disagree).

## Folder slugs and ids

Folder names are fixed by `CATALOG` in `n8n_builder.py` (`P01-error-handler`, `P02-retry-backoff`, `P03-idempotency`,
`P04-rate-limiting`, `P05-sub-workflows`, `P06-testing`, `P07-secrets`, `P08-observability`). A caller references a
pattern's workflow with `catalog_id("P03")`; the error workflow of every item is `catalog_id("P01")`.

## Sub-workflow contract (P05 rules, apply to every shipped building block)

* Trigger: `Execute Workflow Trigger` v1.1 with `inputSource: passthrough`.
* Input: one item per call, fields documented in the README (`{external_id, scope}` etc.). Validate first; fail with
  `Stop and Error` on missing input so the caller's error workflow sees a clear message.
* Output: a single item, flat JSON, includes `ok: boolean` and a `response` string (so AI tool wrappers can use it too).
* No side effects other than the documented ones; idempotent where possible.
* Never depends on a paid service; every service call is retried (`.retry`) where safe.
* Version changes that break the contract get a new folder (`P03b-...`), never a silent edit.

## Screenshot and test folder for patterns

* `assets/screenshot.png` of the sub-workflow canvas (render with `scripts/render-preview.py patterns/<folder>`).
* `test/` contains a sample input item and the expected output (`input.json`, `expected.json`).
