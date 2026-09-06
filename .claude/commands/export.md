---
description: Export workflows from the running n8n back into the repo folders (credentials stripped, pretty-printed), then show what changed.
argument-hint: [ID ...]
allowed-tools: Bash(bash scripts/*), Bash(git diff:*), Bash(git status:*), Bash(python:*), Read
---

Run `bash scripts/export-workflows.sh $ARGUMENTS` (all workflows when no IDs). The script exports with the n8n CLI,
strips credential values, `pinData`, and instance metadata, and writes each workflow into its folder by matching the
top-level `id`. Then run `git status --short workflows patterns` and `python scripts/validate.py` and summarise the
diff per folder. Flag any exported workflow whose id does not map to a folder (it needs a folder or should be deleted
from n8n).
