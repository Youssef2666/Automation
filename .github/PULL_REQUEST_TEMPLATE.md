## What

<!-- One or two lines. Catalog ids touched, e.g. "T03 polling with Redis cursor; P03 Used-by list". -->

## Why

<!-- Link the PRD row (docs/PRD.md section 9), issue, or ADR. -->

## Evidence

<!-- Execution id from `python scripts/dev/executions.py --workflow <ID> --last 1`, or a screenshot of the
     successful execution. For stack changes: output of `python scripts/dev/smoke.py`. -->

## Checklist

- [ ] `python scripts/validate.py --strict --docs` passes locally
- [ ] `assets/screenshot.png` present, at least 1200 px wide, matches the current canvas
- [ ] README front-matter complete (`id`, `title`, `category`, `difficulty`, `status`, `patterns`, `services`, `tested_on`) and `status` is honest
- [ ] No secrets, no `.env`, no real email addresses (only `@lab.local` / `@example.com`), no real people or companies
- [ ] Imported and executed live on n8n 2.37.10 against seeded data only (core path needs no external account)
- [ ] `workflow.json` regenerated from its authoring script (no hand-edited ids or connections)
- [ ] Pattern READMEs updated under "Used by" where this item references them
- [ ] Any deviation from `docs/PRD.md` has an ADR in `docs/decisions/`
