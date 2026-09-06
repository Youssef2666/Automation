---
description: Import one or more workflow folders into the running n8n (CLI import, optional publish), then verify they appear.
argument-hint: [folder ...] [--publish]
allowed-tools: Bash(bash scripts/*), Bash(docker compose:*), Bash(python:*), Bash(curl:*), Read
---

Run `bash scripts/import-workflows.sh $ARGUMENTS` (no folders = every folder under workflows/ and patterns/, in
dependency order: patterns first). Then `python scripts/dev/list-workflows.py` to confirm each imported id/name.

If an import fails, quote the exact n8n error, identify the node/parameter, fix the authoring script (not the JSON),
regenerate, and retry. Publish only what the README marks `autopublish: true` unless `--publish` was given.
