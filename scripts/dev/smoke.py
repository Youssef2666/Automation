#!/usr/bin/env python
"""smoke.py - prove the core loop works end to end and time it (PRD goal G2: < 10 minutes).

Checks: n8n healthz, mock-api, Mailpit API, MinIO liveness, demo db row counts, then fires the T01 webhook with
workflows/T01-webhook-to-database/test/payload.json (if the folder exists), verifies the row landed in
demo.webhook_events and prints the execution status.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _n8n import REPO, api, base_url, psql  # noqa: E402

T0 = time.time()
FAILS: list[str] = []


def check(label: str, fn):
    try:
        val = fn()
        print(f"  ok   {label}: {val}")
        return val
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL {label}: {exc}")
        FAILS.append(label)
        return None


def get(url: str, timeout: int = 10) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
        return r.read().decode("utf-8", "replace")


def post_json(url: str, payload: dict, headers: dict | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        return e.code, e.read().decode("utf-8", "replace")


def main() -> int:
    base = base_url()
    print("== services")
    check("n8n /healthz", lambda: json.loads(get(base + "/healthz")).get("status"))
    check("mock-api /health", lambda: json.loads(get("http://localhost:8080/health")).get("status"))
    check("mailpit API", lambda: f"{json.loads(get('http://localhost:8025/api/v1/messages')).get('total', 0)} messages")
    check("minio live", lambda: "live" if urllib.request.urlopen("http://localhost:9000/minio/health/live", timeout=5).status == 200 else "?")  # noqa: S310
    print("== demo database")
    for table in ("customers", "products", "orders", "tickets", "employees"):
        check(f"count {table}", lambda t=table: psql(f"select count(*) from {t}"))
    print("== n8n API")
    wfs = check("workflows via public API", lambda: f"{len(api('GET', '/workflows?limit=250').get('data', []))} imported")

    t01 = REPO / "workflows" / "T01-webhook-to-database"
    if t01.exists():
        print("== T01 webhook")
        payload = json.loads((t01 / "test" / "payload.json").read_text(encoding="utf-8"))
        ext = payload.get("external_id", "smoke")
        payload["external_id"] = f"{ext}-smoke-{int(time.time())}"
        status, body = post_json(base + "/webhook/t01-orders", payload)
        print(f"  POST /webhook/t01-orders -> {status} {body[:200]}")
        if status not in (200, 201, 202):
            FAILS.append("T01 webhook")
        else:
            time.sleep(2)
            check("row in webhook_events", lambda: psql(f"select count(*) from webhook_events where external_id = '{payload['external_id']}'"))
            check("T01 last execution", lambda: api("GET", "/executions?limit=1&workflowId=" + json.loads((t01 / "workflow.json").read_text(encoding="utf-8"))["id"]).get("data", [{}])[0].get("status"))
    else:
        print("== T01 folder not present yet - webhook test skipped")

    mins = (time.time() - T0) / 60
    print(f"\n== smoke finished in {mins:.1f} min with {len(FAILS)} failure(s)" + (": " + ", ".join(FAILS) if FAILS else ""))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
