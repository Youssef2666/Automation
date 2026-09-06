#!/usr/bin/env python
"""executions.py [--workflow ID] [--last N] [--status error|success] [--id EXEC_ID]

Show recent executions from the public API. With --id, prints the failing node and error message
(and the last node's output sample) - the fastest way to see why a run failed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _n8n import REPO, api  # noqa: E402


def resolve(arg: str | None) -> str | None:
    if not arg or len(arg) == 16:
        return arg
    for base in ("workflows", "patterns"):
        for wf in (REPO / base).glob(f"{arg.upper()}-*/workflow.json"):
            return json.loads(wf.read_text(encoding="utf-8"))["id"]
    raise SystemExit(f"no workflow folder for {arg}")


def show_one(exec_id: str) -> None:
    ex = api("GET", f"/executions/{exec_id}?includeData=true")
    print(f"execution {ex['id']}  workflow={ex.get('workflowId')}  status={ex.get('status')}  mode={ex.get('mode')}")
    print(f"started {ex.get('startedAt')}  stopped {ex.get('stoppedAt')}")
    data = ex.get("data") or {}
    result = data.get("resultData") or {}
    err = result.get("error")
    if err:
        node = (err.get("node") or {}).get("name") if isinstance(err.get("node"), dict) else err.get("node")
        print(f"ERROR in node: {node}\n  {err.get('message')}")
        desc = err.get("description")
        if desc:
            print(f"  {desc}")
    last = result.get("lastNodeExecuted")
    print(f"last node executed: {last}")
    run = (result.get("runData") or {}).get(last) or []
    if run:
        out = run[-1].get("data", {}).get("main", [[]])
        items = out[0] if out else []
        if items:
            sample = items[0].get("json", {})
            print("last node first item: " + json.dumps(sample, ensure_ascii=False)[:600])
        nerr = run[-1].get("error")
        if nerr:
            print(f"node error: {nerr.get('message')}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workflow", help="catalog id (T01) or workflow id")
    ap.add_argument("--last", type=int, default=10)
    ap.add_argument("--status", choices=["error", "success", "waiting", "running", "canceled"])
    ap.add_argument("--id", help="execution id to inspect")
    args = ap.parse_args()
    if args.id:
        show_one(args.id)
        return 0
    q = f"/executions?limit={args.last}"
    wid = resolve(args.workflow)
    if wid:
        q += f"&workflowId={wid}"
    if args.status:
        q += f"&status={args.status}"
    res = api("GET", q)
    rows = res.get("data", [])
    for ex in rows:
        print(f"{ex['id']:>8}  {ex.get('status', '?'):9} {ex.get('mode', ''):8} {ex.get('startedAt', '')[:19]}  wf={ex.get('workflowId')}")
    if not rows:
        print("no executions")
    elif args.last == 1:
        show_one(str(rows[0]["id"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
