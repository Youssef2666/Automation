#!/usr/bin/env python
"""run.py - live check for P07 (sub-workflows cannot be started from the CLI with input).

Builds a throwaway caller with the builder DSL (Manual Trigger -> cases -> Execute Workflow P07, error lane kept),
imports it into the running n8n, executes it with the n8n CLI, reads the execution back through the public API,
asserts the three cases below, and deletes the caller again. Needs the core stack up and `bash scripts/setup.sh`
run once (API key + credentials). Exit code 1 when any case fails.

  python patterns/P07-secrets/test/run.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "scripts" / "dev"))
sys.path.insert(0, str(REPO / ".claude" / "skills" / "n8n-workflow-json"))
from _n8n import api, compose, n8n_cli  # noqa: E402
from n8n_builder import Workflow, catalog_id, code, execute_workflow, manual_trigger, noop  # noqa: E402

CALLER_ID = "ALP07SecretsTest"
P07_ID = catalog_id("P07")
INPUT = json.loads((HERE / "input.json").read_text(encoding="utf-8"))
CASES = [
    {"name": "post_allowed", "input": INPUT, "expect": {"ok": True, "status": 200, "method": "POST"}},
    {"name": "get_allowed", "input": {"url": "http://mock-api:8080/secure/ping"},
     "expect": {"ok": True, "status": 200, "method": "GET"}},
    {"name": "refused_host", "input": {"url": "http://example.com/collect", "method": "POST", "body": {"event": "p07.demo"}},
     "expect_error": "not in allowed_hosts"},
]


def build_caller() -> dict:
    wf = Workflow("P07", "secrets-test-caller", "P07 test caller", tags=["pattern", "P07", "test"], id=CALLER_ID,
                  description="Throwaway caller created by patterns/P07-secrets/test/run.py; deleted after the run.")
    trg = manual_trigger(wf, "Run once (CLI)")
    cases = code(wf, "Cases", "const cases = " + json.dumps([c["input"] for c in CASES]) +
                 ";\nreturn cases.map((c) => ({ json: c }));")
    call = execute_workflow(wf, "Signed request (P07)", P07_ID, mode="each").on_error("continueErrorOutput")
    done = noop(wf, "Done")
    wf.chain(trg, cases, call, done)
    wf.connect(call, done, out=1)
    return wf.build()


def import_caller(doc: dict) -> None:
    tmp = Path(tempfile.mkdtemp()) / "p07-caller.json"
    tmp.write_text(json.dumps(doc), encoding="utf-8")
    try:
        compose("cp", str(tmp), "n8n:/tmp/p07-caller.json")
        res = n8n_cli("import:workflow", "--input=/tmp/p07-caller.json")
        if res.returncode != 0 or "Successfully" not in (res.stdout + res.stderr):
            raise RuntimeError("import failed: " + (res.stdout + res.stderr)[-400:])
    finally:
        compose("exec", "-T", "n8n", "rm", "-f", "/tmp/p07-caller.json", check=False)
        shutil.rmtree(tmp.parent, ignore_errors=True)


def latest_execution(workflow_id: str) -> dict:
    rows = api("GET", f"/executions?workflowId={workflow_id}&limit=1").get("data") or []
    if not rows:
        raise RuntimeError(f"no execution found for {workflow_id}")
    return api("GET", f"/executions/{rows[0]['id']}?includeData=true")


def main() -> int:
    import_caller(build_caller())
    failures: list[str] = []
    try:
        res = n8n_cli("execute", f"--id={CALLER_ID}", timeout=300)
        if res.returncode != 0:
            print((res.stdout + res.stderr)[-800:])
            raise RuntimeError("n8n execute failed")
        ex = latest_execution(CALLER_ID)
        run = ((ex.get("data") or {}).get("resultData") or {}).get("runData") or {}
        lanes = (run.get("Signed request (P07)") or [{}])[-1].get("data", {}).get("main") or [[], []]
        # continueErrorOutput appends the error lane after the node's own outputs (Execute Workflow v1.1 reports it
        # at index 2 in n8n 2.37), so read "everything after lane 0" as the error lane.
        ok_items = [i.get("json", {}) for i in (lanes[0] if len(lanes) > 0 else [])]
        err_items = [i.get("json", {}) for lane in lanes[1:] for i in (lane or [])]
        print(f"caller execution {ex['id']} status={ex.get('status')} -> {len(ok_items)} result item(s), "
              f"{len(err_items)} error item(s)")
        if "--verbose" in sys.argv:
            for name, runs in run.items():
                for r in runs:
                    for li, lane in enumerate(r.get("data", {}).get("main") or []):
                        for it in lane or []:
                            print(f"  [{name}] lane {li}: {json.dumps(it.get('json'), ensure_ascii=False)[:240]}")
        for case in CASES:
            name = case["name"]
            if "expect" in case:
                hit = next((i for i in ok_items if i.get("url") == case["input"]["url"]
                            and i.get("method") == case["expect"]["method"]), None)
                if not hit:
                    failures.append(f"{name}: no result item for {case['input']['url']}")
                    continue
                bad = {k: (hit.get(k), v) for k, v in case["expect"].items() if hit.get(k) != v}
                secret_leak = "lab-demo-key" in json.dumps(hit)
                if bad or secret_leak:
                    failures.append(f"{name}: mismatch {bad}" + (" and the key leaked into execution data" if secret_leak else ""))
                else:
                    print(f"  PASS {name}: {hit.get('response')}")
            else:
                msg = " | ".join(str(i.get("error") or i) for i in err_items)
                if case["expect_error"] in msg:
                    print(f"  PASS {name}: refused before any request ({case['expect_error']!r} in error lane)")
                else:
                    failures.append(f"{name}: expected an error containing {case['expect_error']!r}, got: {msg[:200] or 'nothing'}")
        subs = api("GET", f"/executions?workflowId={P07_ID}&limit={len(CASES)}").get("data") or []
        print("P07 sub-workflow executions: " + ", ".join(f"{s['id']} {s.get('status')}" for s in subs))
    finally:
        if "--keep" in sys.argv:
            print(f"caller {CALLER_ID} kept (delete it with the editor or the public API)")
        else:
            try:
                api("DELETE", f"/workflows/{CALLER_ID}")
                print("caller deleted")
            except Exception as exc:  # noqa: BLE001
                print(f"warning: could not delete caller {CALLER_ID}: {exc}")
    if failures:
        print("FAILED:\n  " + "\n  ".join(failures))
        return 1
    print(f"{len(CASES)} passed, 0 failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
