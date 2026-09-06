#!/usr/bin/env python
"""replay.py [cases.json] - run the P06 test plan from the host (standard library only).

Same cases as the n8n harness workflow; in-network hostnames are mapped to the published ports.
Prints one line per case and exits 1 when any case fails (skipped cases do not fail the run).
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

HOST_MAP = {"http://n8n:5678": "http://localhost:5678", "http://mock-api:8080": "http://localhost:8080",
            "http://docgen:9010": "http://localhost:9010", "http://mailpit:8025": "http://localhost:8025"}


def call(case: dict) -> tuple[int, object, str | None]:
    url = case["url"]
    for k, v in HOST_MAP.items():
        url = url.replace(k, v)
    data = json.dumps(case["body"]).encode() if case.get("body") is not None else None
    req = urllib.request.Request(url, data=data, method=case.get("method", "GET"))
    for k, v in (case.get("headers") or {}).items():
        req.add_header(k, v)
    if data is not None and "Content-Type" not in (case.get("headers") or {}):
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310
            raw = r.read().decode("utf-8", "replace")
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read().decode("utf-8", "replace"), e.code
    except (urllib.error.URLError, OSError) as e:
        return 0, None, str(e)
    try:
        body: object = json.loads(raw)
    except ValueError:
        body = raw
    return status, body, None


def assert_case(case: dict, status: int, body: object) -> tuple[str, str]:
    expected = case["expect_status"] if isinstance(case["expect_status"], list) else [case["expect_status"]]
    if case.get("optional") and status in (0, 404):
        return "SKIP", "skipped (target not available)"
    problems = []
    if status not in expected:
        problems.append(f"status {status}, expected {' or '.join(map(str, expected))}")
    if "expect_path" in case:
        got: object = body
        for key in case["expect_path"].split("."):
            got = got.get(key) if isinstance(got, dict) else None
        if got != case.get("expect_value"):
            problems.append(f"{case['expect_path']} = {got!r}, expected {case.get('expect_value')!r}")
    return ("FAIL", "; ".join(problems)) if problems else ("PASS", "ok")


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("cases.json")
    cases = json.loads(path.read_text(encoding="utf-8"))
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    for case in cases:
        status, body, err = call(case)
        verdict, detail = assert_case(case, status, body)
        if err and verdict != "SKIP":
            verdict, detail = "FAIL", err
        counts[verdict] += 1
        print(f"{verdict:4}  {case['name']:45} [{status}] {detail}")
    print(f"\n{counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped of {len(cases)}")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
