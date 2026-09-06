#!/usr/bin/env python
"""node-types.py [<node type>] [--versions] [--dump FILE]

Reads the live node catalogue (GET /types/nodes.json) from the running n8n and prints, for one node type,
its versions and the parameter schema (name, type, default, options, displayOptions). Use it whenever a
parameter name or shape is uncertain while authoring workflow JSON.

  python scripts/dev/node-types.py n8n-nodes-base.postgres
  python scripts/dev/node-types.py --versions            # every type with its versions
  python scripts/dev/node-types.py --dump scripts/known-nodes.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _n8n import Session, base_url, read_env  # noqa: E402


def load() -> list[dict]:
    env = read_env()
    sess = Session(base_url(env))
    status, data = sess.get("/types/nodes.json", raise_for_status=False)
    if status == 401:
        sess.login(env.get("N8N_OWNER_EMAIL", "owner@lab.local"), env.get("N8N_OWNER_PASSWORD", "LabOwner2026x"))
        status, data = sess.get("/types/nodes.json")
    if not isinstance(data, list):
        raise SystemExit(f"unexpected response: {status}")
    return data


def versions_of(d: dict) -> list:
    v = d.get("version")
    return v if isinstance(v, list) else [v]


def describe(node: dict, indent: str = "  ") -> None:
    print(f"{node['name']}  displayName='{node.get('displayName')}'  versions={versions_of(node)}")
    print(f"{indent}inputs={node.get('inputs')}  outputs={node.get('outputs')}")
    creds = [c.get("name") for c in node.get("credentials", [])]
    if creds:
        print(f"{indent}credentials={creds}")
    for prop in node.get("properties", []):
        opts = ""
        if prop.get("type") == "options":
            opts = " options=" + ",".join(str(o.get("value")) for o in prop.get("options", [])[:25])
        show = prop.get("displayOptions", {}).get("show")
        cond = f"  show={json.dumps(show)}" if show else ""
        print(f"{indent}- {prop.get('name')}: {prop.get('type')} default={json.dumps(prop.get('default'))!s:.60}{opts}{cond}")
        if prop.get("type") in ("collection", "fixedCollection"):
            for opt in prop.get("options", []):
                if "values" in opt:
                    print(f"{indent}    [{opt.get('name')}] " + ", ".join(v.get("name", "") for v in opt.get("values", [])))
                else:
                    print(f"{indent}    . {opt.get('name')}: {opt.get('type')}")


def main() -> int:
    args = sys.argv[1:]
    nodes = load()
    by_name: dict[str, list[dict]] = {}
    for n in nodes:
        by_name.setdefault(n["name"], []).append(n)
    if "--dump" in args:
        out = Path(args[args.index("--dump") + 1])
        out.write_text(json.dumps({k: sorted({str(v) for d in ds for v in versions_of(d)}) for k, ds in sorted(by_name.items())},
                                  indent=1), encoding="utf-8")
        print(f"wrote {out} ({len(by_name)} types)")
        return 0
    if "--versions" in args or not args:
        for name, ds in sorted(by_name.items()):
            vs = sorted({v for d in ds for v in versions_of(d)}, key=lambda x: float(x))
            print(f"{name:70} {vs}")
        return 0
    target = args[0]
    matches = by_name.get(target) or [n for k, ns in by_name.items() if target.lower() in k.lower() for n in ns]
    if not matches:
        print(f"no node type matching {target}")
        return 1
    for node in matches:
        describe(node)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
