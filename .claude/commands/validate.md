---
description: Run the repo validator (folder contract, workflow JSON structure, secret scan) on everything or on given folders, then fix what it reports.
argument-hint: [folder ...] [--strict]
allowed-tools: Bash(python:*), Read, Edit, Glob, Grep
---

Run `python scripts/validate.py $ARGUMENTS` and show the summary.

For every error: open the file, fix the root cause (never suppress a rule), re-run until clean. Warnings about
`status: in-progress` items are acceptable; errors are not. If a rule itself is wrong, fix it in `scripts/validate.py`
and mirror the change in `.claude/hooks/_common.py` (secret patterns) so the hook and CI stay in sync.
