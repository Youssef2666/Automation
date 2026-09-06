#!/usr/bin/env python
"""list-workflows.py - list workflows in the running n8n (id, active, name) via the public API."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _n8n import api  # noqa: E402


def main() -> int:
    cursor = None
    rows = []
    while True:
        res = api("GET", "/workflows?limit=250" + (f"&cursor={cursor}" if cursor else ""))
        rows.extend(res.get("data", []))
        cursor = res.get("nextCursor")
        if not cursor:
            break
    rows.sort(key=lambda w: w.get("name", ""))
    for w in rows:
        print(f"{w['id']:18} {'ACTIVE ' if w.get('active') else 'draft  '} {w.get('name')}")
    print(f"{len(rows)} workflow(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
