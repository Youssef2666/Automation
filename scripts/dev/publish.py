#!/usr/bin/env python
"""publish.py <workflow-id|catalog-id> [--off] - publish (activate) or unpublish a workflow via the public API."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _n8n import REPO, api  # noqa: E402


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
    action = "deactivate" if "--off" in sys.argv else "activate"
    res = api("POST", f"/workflows/{wid}/{action}")
    print(f"{action}d {res.get('name', wid)} (active={res.get('active')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
