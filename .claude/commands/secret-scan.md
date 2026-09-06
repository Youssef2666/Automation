---
description: Scan the working tree and git history for credential-looking strings, real emails, and committed .env files; report and remediate.
allowed-tools: Bash(python:*), Bash(git log:*), Bash(git grep:*), Bash(git ls-files:*), Read
---

1. `python scripts/validate.py --secrets-only` (working tree; same patterns as the Claude hook and CI).
2. `git ls-files | grep -E '(^|/)\.env(\.|$)' | grep -v example` - any real env file tracked?
3. `git log -p --all -S 'PRIVATE KEY' --oneline | head` and `git log --all -p | python scripts/validate.py --secrets-stdin`
   to catch secrets that were committed then removed.

Report findings with file, line, and pattern name. For working-tree hits: replace with placeholders. For history hits:
explain that the secret must be rotated and the history rewritten by the user (do not rewrite history yourself).
