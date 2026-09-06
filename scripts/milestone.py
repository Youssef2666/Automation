#!/usr/bin/env python
"""milestone.py - progress against the PRD milestones (M0-M5) and goals (G1-G6), plus what to build next.

  python scripts/milestone.py            # human report
  python scripts/milestone.py --json     # machine-readable
  python scripts/milestone.py --next 10  # longer "next items" list

Status comes from the front-matter of workflows/*/README.md and patterns/*/README.md (planned | in-progress |
shipped); a catalog id without a folder is reported as "missing". M0 is a set of file-existence checks.
"Next items to build" honours the dependency order: the patterns an item references (front-matter `patterns:`,
`depends_on:`, or the static adoption table from the pattern-authoring skill) come before the item itself.
Standard library only.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
import validate  # noqa: E402


def _load_build_matrix():
    spec = importlib.util.spec_from_file_location("build_matrix", SCRIPT_DIR / "build-matrix.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


build_matrix = _load_build_matrix()
PLANNED = build_matrix.PLANNED

# docs/PRD.md section 13
MILESTONES: list[tuple[str, str, list[str], str]] = [
    ("M0", "Foundation", [], "docker compose --profile core up works on a clean machine; CI green"),
    ("M1", "Core loop", ["P01", "P02", "T01", "T02", "T03", "D01"], "Error handling proven; first screenshots in"),
    ("M2", "Documents", ["R01", "R02", "R03", "R04"], "R03/R04 chain demoed end-to-end with a GIF"),
    ("M3", "AI", ["P05", "A01", "A02", "A03"], "RAG chatbot runs offline on 8 GB RAM (ai profile)"),
    ("M4", "Ops & business", ["P03", "P04", "B01", "B02", "B03", "B04", "O01", "O02", "O03", "O04", "O05"],
     "O04 self-export loop running nightly"),
    ("M5", "Polish & launch", [], "25+ workflows, all acceptance criteria met, tool-comparison.md + hero GIF"),
]
# M5 = everything the earlier milestones do not name
_named = {i for _, _, ids, _ in MILESTONES for i in ids}
MILESTONES[5] = (MILESTONES[5][0], MILESTONES[5][1], [i for i in PLANNED if i not in _named], MILESTONES[5][3])

# M0 file checks: (label, candidate paths - any one existing counts)
M0_CHECKS: list[tuple[str, tuple[str, ...]]] = [
    ("compose stack", ("docker-compose.yml", "docker/docker-compose.yml")),
    ("seed data", ("seed/seed.sql",)),
    ("seed schema", ("seed/schema.sql",)),
    ("env example", (".env.example",)),
    ("validate script", ("scripts/validate.py",)),
    ("root README", ("README.md",)),
    ("CI validate workflow", (".github/workflows/validate.yml",)),
    ("setup script", ("scripts/setup.sh",)),
]

# Static adoption table (pattern-authoring skill) used until folders declare `patterns:` themselves.
STATIC_ADOPTION: dict[str, tuple[str, ...]] = {
    "P02": ("T03", "D03", "M05", "B01"),
    "P03": ("T01", "M02", "B01", "D04"),
    "P04": ("D02", "D03", "R02", "A06"),
    "P05": ("A05", "B04", "P01"),
    "P06": ("T01", "T05", "M02", "B01"),
    "P08": ("O05", "M01", "P01"),
}
STATIC_DEPENDS: dict[str, tuple[str, ...]] = {"B04": ("A02",)}
GOAL_WORKFLOWS_SHIPPED = 25
GOAL_CATEGORIES = 8
GOAL_PATTERNS = 6
GOAL_PATTERN_REFS = 2


def item_status(it: dict[str, Any]) -> str:
    if it["folder"] is None:
        return "missing"
    return it["status"]


def dependencies(cid: str, items: dict[str, dict[str, Any]]) -> list[str]:
    """Ids that should exist before `cid`: referenced patterns (+ depends_on) or the static adoption table."""
    it = items.get(cid) or {}
    deps: list[str] = []
    for p in it.get("patterns") or []:
        deps.append(p)
    for d in it.get("depends_on") or []:
        deps.append(d)
    if not deps and not cid.startswith("P"):
        # patterns never block other patterns (P01 "uses" P05/P08 blocks but ships first per the PRD)
        for pid, users in STATIC_ADOPTION.items():
            if cid in users:
                deps.append(pid)
        deps.extend(STATIC_DEPENDS.get(cid, ()))
        if "P01" not in deps:
            deps.insert(0, "P01")  # every workflow routes errors to P01
    seen: set[str] = set()
    return [d for d in deps if d in items and d != cid and not (d in seen or seen.add(d))]


def next_items(items: dict[str, dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Unshipped items in milestone order, each preceded by its unshipped dependencies."""
    order: list[str] = []
    seen: set[str] = set()

    def visit(cid: str, stack: tuple[str, ...] = ()) -> None:
        if cid in seen or cid in stack:
            return
        if items[cid]["status"] == "shipped":
            seen.add(cid)
            return
        for dep in dependencies(cid, items):
            visit(dep, stack + (cid,))
        seen.add(cid)
        order.append(cid)

    for _, _, ids, _ in MILESTONES:
        for cid in ids:
            visit(cid)
    out = []
    for cid in order[:limit]:
        it = items[cid]
        out.append({"id": cid, "title": it["title"], "status": item_status(it), "category": it["category"],
                    "depends_on": dependencies(cid, items)})
    return out


def milestone_report(root: Path, items: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    report: list[dict[str, Any]] = []
    for code, name, ids, exit_criteria in MILESTONES:
        if code == "M0":
            checks = []
            for label, cands in M0_CHECKS:
                hit = next((c for c in cands if (root / c).exists()), None)
                checks.append({"id": label, "title": hit or cands[0], "status": "shipped" if hit else "missing"})
            done = sum(c["status"] == "shipped" for c in checks)
            report.append({"code": code, "name": name, "items": checks, "done": done, "total": len(checks),
                           "percent": round(100 * done / len(checks)) if checks else 0, "exit": exit_criteria})
            continue
        rows = [{"id": i, "title": items[i]["title"], "status": item_status(items[i]),
                 "category": items[i]["category"]} for i in ids if i in items]
        done = sum(r["status"] == "shipped" for r in rows)
        report.append({"code": code, "name": name, "items": rows, "done": done, "total": len(rows),
                       "percent": round(100 * done / len(rows)) if rows else 0, "exit": exit_criteria})
    return report


def goals_report(root: Path, items: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    wf = {k: v for k, v in items.items() if not k.startswith("P")}
    pt = {k: v for k, v in items.items() if k.startswith("P")}
    shipped_wf = [k for k, v in wf.items() if v["status"] == "shipped"]
    shipped_pt = [k for k, v in pt.items() if v["status"] == "shipped"]
    cats_covered = sorted({v["category"] for v in items.values() if v["status"] == "shipped"})
    usage = build_matrix.used_by(items)
    refs_ok = [p for p in shipped_pt if len(usage.get(p, [])) >= GOAL_PATTERN_REFS]
    external = [k for k in shipped_wf if wf[k]["external"] and wf[k]["external"].lower() != "none"]
    with_shot = [k for k in shipped_wf + shipped_pt if items[k]["has_screenshot"]]
    readme = root / "README.md"
    readme_text = validate.read_text(readme) if readme.exists() else ""
    ci = root / ".github" / "workflows" / "validate.yml"
    smoke = root / "scripts" / "dev" / "smoke.py"
    return [
        {"code": "G1", "goal": "Breadth: >= 8 categories covered, >= 25 workflows shipped",
         "measure": f"{len(cats_covered)}/{GOAL_CATEGORIES} categories with a shipped item, "
                    f"{len(shipped_wf)}/{GOAL_WORKFLOWS_SHIPPED} workflows shipped",
         "met": len(cats_covered) >= GOAL_CATEGORIES and len(shipped_wf) >= GOAL_WORKFLOWS_SHIPPED},
        {"code": "G2", "goal": "Clone -> first workflow running < 10 min",
         "measure": ("smoke test available (python scripts/dev/smoke.py prints elapsed time)" if smoke.exists()
                     else "scripts/dev/smoke.py missing") + "; not measured here",
         "met": None},
        {"code": "G3", "goal": "Fully offline / free core path",
         "measure": f"{len(shipped_wf) - len(external)}/{len(shipped_wf)} shipped workflows declare no external "
                    f"service; {len(external)} document an optional one ({', '.join(external) or '-'})",
         "met": True if shipped_wf else None},
        {"code": "G4", "goal": f">= {GOAL_PATTERNS} patterns, each referenced by >= {GOAL_PATTERN_REFS} workflows",
         "measure": f"{len(shipped_pt)}/{GOAL_PATTERNS} patterns shipped, {len(refs_ok)} of them referenced by "
                    f">= {GOAL_PATTERN_REFS} workflows",
         "met": len(shipped_pt) >= GOAL_PATTERNS and len(refs_ok) == len(shipped_pt) and bool(shipped_pt)},
        {"code": "G5", "goal": "Legible in 60 s: README matrix + screenshot per workflow",
         "measure": f"matrix markers {'present' if '<!-- MATRIX:START -->' in readme_text else 'MISSING'} in README; "
                    f"screenshots {len(with_shot)}/{len(shipped_wf) + len(shipped_pt)} shipped items",
         "met": ("<!-- MATRIX:START -->" in readme_text and len(with_shot) == len(shipped_wf) + len(shipped_pt))
         if (shipped_wf or shipped_pt) else None},
        {"code": "G6", "goal": "CI validates every workflow JSON on every push",
         "measure": f".github/workflows/validate.yml {'present' if ci.exists() else 'MISSING'}; status on main: "
                    "check GitHub Actions",
         "met": ci.exists()},
    ]


STATUS_MARK = {"shipped": "[x]", "in-progress": "[~]", "planned": "[ ]", "missing": "[ ]"}


def print_human(root: Path, items: dict[str, dict[str, Any]], milestones: list[dict[str, Any]],
                goals: list[dict[str, Any]], nxt: list[dict[str, Any]]) -> None:
    c = build_matrix.counts(items)
    print(f"Automation Lab progress - {c['workflows_shipped']}/{c['workflows_total']} workflows shipped, "
          f"{c['patterns_shipped']}/{c['patterns_total']} patterns shipped, "
          f"{c['workflows_building'] + c['patterns_building']} building")
    print()
    for m in milestones:
        print(f"{m['code']} - {m['name']}: {m['done']}/{m['total']} ({m['percent']}%)   exit: {m['exit']}")
        for it in m["items"]:
            mark = STATUS_MARK.get(it["status"], "[ ]")
            tag = "" if it["status"] == "shipped" else f"  ({it['status']})"
            print(f"   {mark} {it['id']:<22} {it['title']}{tag}" if m["code"] == "M0"
                  else f"   {mark} {it['id']}  {it['title']}{tag}")
        print()
    print("Goals")
    for g in goals:
        mark = "[x]" if g["met"] else ("[?]" if g["met"] is None else "[ ]")
        print(f"   {mark} {g['code']}  {g['goal']}")
        print(f"        {g['measure']}")
    print()
    print("Next items to build (patterns before the workflows that use them):")
    if not nxt:
        print("   nothing left - everything is shipped")
    for i, it in enumerate(nxt, 1):
        deps = f"  needs: {', '.join(it['depends_on'])}" if it["depends_on"] else ""
        print(f"   {i}. {it['id']} - {it['title']}  [{it['status']}]{deps}")
    empty = [i for i, it in items.items() if it["folder"] is None]
    building = [i for i, it in items.items() if it["status"] == "in-progress"]
    print()
    print(f"Blunt view: {len(empty)} catalog ids have no folder at all, {len(building)} are in progress"
          + (f" ({', '.join(building)})" if building else "") + ".")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="repo root (default: parent of scripts/)")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--next", type=int, default=8, help="how many next items to list (default 8)")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    root = Path(args.root).resolve()
    items = build_matrix.build_catalog(root)
    milestones = milestone_report(root, items)
    goals = goals_report(root, items)
    nxt = next_items(items, args.next)
    if args.json:
        payload = {
            "counts": build_matrix.counts(items),
            "milestones": milestones,
            "goals": goals,
            "next": nxt,
            "items": {k: {"title": v["title"], "category": v["category"], "status": item_status(v),
                          "folder": v["folder"], "patterns": v["patterns"]} for k, v in items.items()},
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print_human(root, items, milestones, goals, nxt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
