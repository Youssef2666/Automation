#!/usr/bin/env python
"""run-workflow.py <catalog-id|workflow-id> - execute a workflow once via the n8n CLI inside the container.

Works for workflows that start from a Manual/Schedule trigger (the trigger emits one item).
Webhook/Form/Chat workflows are exercised with curl against their URL instead (see each README).
Prints the CLI output and then the latest execution summary.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _n8n import REPO, n8n_cli  # noqa: E402


def resolve(arg: str) -> str:
    if len(arg) == 16:
        return arg
    for base in ("workflows", "patterns"):
        for wf in (REPO / base).glob(f"{arg.upper()}-*/workflow.json"):
            return json.loads(wf.read_text(encoding="utf-8"))["id"]
    raise SystemExit(f"no workflow folder for {arg}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    wid = resolve(sys.argv[1])
    res = n8n_cli("execute", f"--id={wid}", timeout=900)
    out = (res.stdout + res.stderr).strip()
    print(out[-3000:])
    subprocess.run([sys.executable, str(Path(__file__).with_name("executions.py")), "--workflow", wid, "--last", "1"],
                   cwd=REPO, check=False)
    return 0 if res.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
