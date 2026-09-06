---
paths:
  - "scripts/**"
  - ".github/**"
---

# Rules for scripts/ and .github/

- Python scripts: standard library only (plus optional Pillow/PyYAML guarded by try/except), Python 3.10+, `--help`
  via argparse, exit code 1 on failure, cross-platform paths (pathlib), UTF-8 explicit.
- Shell scripts: `#!/usr/bin/env bash`, `set -euo pipefail`, run from repo root, work in Git Bash on Windows,
  call n8n through `docker compose exec -T n8n n8n <command>`.
- `scripts/validate.py` is the single source of truth for the folder contract and the secret patterns; the Claude
  hook in `.claude/hooks/_common.py` mirrors its patterns - keep both in sync.
- CI workflows pin action versions (`actions/checkout@v4`), run on `ubuntu-latest`, and never need secrets for the
  validate/matrix/links jobs. The secret scan is blocking.
- Never print `.env` contents or decrypted credentials in any script or CI log.
