---
description: Audit one catalog item against the "done" acceptance criteria with evidence, and flip its status to shipped only if everything passes.
argument-hint: <ID>
allowed-tools: Bash(python:*), Bash(bash scripts/*), Bash(curl:*), Bash(docker compose:*), Read, Edit, Glob, Grep
---

Audit item **$1** against the acceptance checklist in `.claude/skills/workflow-folder-contract/SKILL.md`.

For each criterion produce PASS/FAIL with evidence:
1. Imports cleanly - `bash scripts/import-workflows.sh <folder>` output (or "stack down: not verified").
2. Executes end-to-end on seeded data - run the README "Try it" commands and `python scripts/dev/executions.py --workflow $1 --last 1`.
3. README lists every credential with setup steps - compare against `credentials` in workflow.json.
4. Screenshot present, >= 1200 px, current - `python scripts/validate.py <folder>` reports width; note if it is an auto-render.
5. `python scripts/validate.py <folder>` passes.
6. Row in the coverage matrix - `python scripts/build-matrix.py --check`.
7. References >= 1 pattern where applicable, and the pattern README lists $1 under "Used by".

If all PASS: set `status: shipped` in the README front-matter and regenerate the matrix. Otherwise leave the status,
list the failing criteria with the concrete fix, and offer to delegate to `workflow-builder`.
