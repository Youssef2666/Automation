#!/usr/bin/env python
"""build-matrix.py - regenerate the coverage matrix, patterns index and stats line in the root README.

Reads the YAML front-matter of every workflows/*/README.md and patterns/*/README.md (id, title, category,
difficulty, status, patterns, services, external, doc_only) through scripts/validate.py, merges it with the static
PLANNED catalog (docs/PRD.md section 9) so all 36 workflows + 8 patterns always appear, and rewrites three blocks:

  <!-- MATRIX:START -->   one table per category (Triggers, Data & ETL, Monitoring, Documents, AI, Business, DevOps)
  <!-- PATTERNS:START --> ID | Pattern | Used by | Status   ("Used by" computed from the workflows' `patterns:` lists)
  <!-- STATS:START -->    "N of 36 workflows shipped · M of 8 patterns"

Flags: --check (exit 1 if README.md would change; CI), --readme PATH, --root PATH.
If README.md does not exist yet, the tables are printed to stdout instead of failing.
Standard library only.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
import validate  # noqa: E402

CATEGORY_ORDER = list(validate.CATEGORY_ORDER)  # Triggers, Data & ETL, Monitoring, Documents, AI, Business, DevOps
STATUS_LABEL = {"planned": "\U0001f4cb planned", "in-progress": "\U0001f6a7 building", "shipped": "\u2705 shipped"}
MARKERS = {"matrix": "MATRIX", "patterns": "PATTERNS", "stats": "STATS"}

# --- static catalog (docs/PRD.md section 9): id -> (title, category, difficulty) --------------------------------
# Used for rows whose folder does not exist yet, so the matrix always shows all 36 workflows + 8 patterns.
PLANNED: dict[str, tuple[str, str, str]] = {
    # 9.1 Triggers
    "T01": ("Webhook to Database", "Triggers", "Beginner"),
    "T02": ("Scheduled Daily Digest", "Triggers", "Beginner"),
    "T03": ("Polling an API Without Webhooks", "Triggers", "Intermediate"),
    "T04": ("IMAP Email Trigger to Attachment Parser", "Triggers", "Intermediate"),
    "T05": ("Form Trigger to Record and Confirmation Email", "Triggers", "Beginner"),
    "T06": ("File Watcher: Process on Drop", "Triggers", "Beginner"),
    "T07": ("Telegram Chat Trigger to Command Router", "Triggers", "Intermediate"),
    # 9.2 Data & ETL
    "D01": ("CSV/XLSX Import with Row-level Validation", "Data & ETL", "Intermediate"),
    "D02": ("Web Scrape to Structured JSON", "Data & ETL", "Intermediate"),
    "D03": ("Multi-source API Aggregation", "Data & ETL", "Advanced"),
    "D04": ("Incremental Sync with Upsert and Dedupe", "Data & ETL", "Advanced"),
    "D05": ("Scheduled DB Dump to MinIO with Rotation", "Data & ETL", "Intermediate"),
    # 9.3 Monitoring
    "M01": ("Uptime Monitor with Escalation", "Monitoring", "Intermediate"),
    "M02": ("GitHub Events to Chat Notification", "Monitoring", "Beginner"),
    "M03": ("RSS Keyword-filtered Digest", "Monitoring", "Beginner"),
    "M04": ("DB Threshold Alert", "Monitoring", "Beginner"),
    "M05": ("Price / Exchange-rate Watcher", "Monitoring", "Intermediate"),
    # 9.4 Documents
    "R01": ("Data to PDF Invoice", "Documents", "Intermediate"),
    "R02": ("Bulk Certificate Generation from CSV", "Documents", "Intermediate"),
    "R03": ("Data to Arabic RTL DOCX/PPTX Report", "Documents", "Advanced"),
    "R04": ("PDF to Structured Fields to Dataset", "Documents", "Advanced"),
    # 9.5 AI
    "A01": ("RAG Chatbot over Repo Docs", "AI", "Advanced"),
    "A02": ("Ticket / Email Classification and Routing", "AI", "Intermediate"),
    "A03": ("Audio to Transcript, Summary and Task List", "AI", "Advanced"),
    "A04": ("Arabic OCR to Structured Data", "AI", "Advanced"),
    "A05": ("Agent with Tools Calling Sub-workflows", "AI", "Advanced"),
    "A06": ("Long Article to Social Variants", "AI", "Intermediate"),
    # 9.6 Business
    "B01": ("Lead Capture, Enrich, CRM Row and Follow-up Sequence", "Business", "Advanced"),
    "B02": ("Booking to Calendar to Reminder Chain", "Business", "Intermediate"),
    "B03": ("Receipt Image to OCR to Sheet to Monthly Rollup", "Business", "Intermediate"),
    "B04": ("Support Inbox Triage, Assign and SLA Timer", "Business", "Advanced"),
    # 9.7 DevOps
    "O01": ("GitHub Actions: Validate JSON and Lint on PR", "DevOps", "Beginner"),
    "O02": ("Actions: Auto-changelog and Release Tagging", "DevOps", "Beginner"),
    "O03": ("Issue Triage Bot: Label, Assign, Stale-close", "DevOps", "Intermediate"),
    "O04": ("n8n to Git: Nightly Workflow Export", "DevOps", "Intermediate"),
    "O05": ("Execution Logs to Postgres to Metabase", "DevOps", "Intermediate"),
    # 9.8 Patterns
    "P01": ("Global Error Handler", "Patterns", "Intermediate"),
    "P02": ("Retry with Exponential Backoff", "Patterns", "Intermediate"),
    "P03": ("Idempotency", "Patterns", "Intermediate"),
    "P04": ("Rate Limiting and Batching", "Patterns", "Intermediate"),
    "P05": ("Sub-workflow Modularity", "Patterns", "Intermediate"),
    "P06": ("Testing and Mock Payloads", "Patterns", "Intermediate"),
    "P07": ("Secrets in a Public Repo", "Patterns", "Beginner"),
    "P08": ("Observability", "Patterns", "Intermediate"),
}
WORKFLOW_IDS = [i for i in PLANNED if not i.startswith("P")]
PATTERN_IDS = [i for i in PLANNED if i.startswith("P")]
TOTAL_WORKFLOWS = len(WORKFLOW_IDS)  # 36
TOTAL_PATTERNS = len(PATTERN_IDS)    # 8

USED_BY_ID_RE = re.compile(r"\b([TDMRABO]\d{2})\b")


# --- catalog ----------------------------------------------------------------------------------------------------

def build_catalog(root: Path) -> dict[str, dict[str, Any]]:
    """PLANNED merged with the folders that exist. Key = catalog id; entry has a `folder` (or None)."""
    items: dict[str, dict[str, Any]] = {}
    for cid, (title, category, difficulty) in PLANNED.items():
        items[cid] = {
            "id": cid, "title": title, "category": category, "difficulty": difficulty, "status": "planned",
            "patterns": [], "services": [], "external": "", "doc_only": cid in validate.DOC_ONLY_IDS,
            "folder": None, "has_screenshot": False, "has_workflow": False, "used_by_readme": None,
        }
    for entry in validate.load_catalog(root):
        cid = entry["id"]
        base = items.get(cid) or {
            "id": cid, "title": entry["title"],
            "category": entry["category"] or validate.LETTER_TO_CATEGORY.get(cid[:1], ""),
            "difficulty": "", "status": "planned", "patterns": [], "services": [], "external": "",
            "doc_only": False, "folder": None, "has_screenshot": False, "has_workflow": False, "used_by_readme": None,
        }
        fm = entry.get("fm") or {}
        if entry["folder"] and base["folder"]:
            print(f"warning: {cid} has two folders ({base['folder']} and {entry['folder']}); using the first",
                  file=sys.stderr)
            continue
        base.update({
            "title": entry["title"] if entry["has_readme"] and fm.get("title") else base["title"],
            "category": entry["category"] or base["category"],
            "difficulty": entry["difficulty"] or base["difficulty"],
            "status": entry["status"] if entry["status"] in validate.STATUSES else "planned",
            "patterns": entry["patterns"],
            "services": entry["services"],
            "external": str(fm.get("external") or "").strip(),
            "doc_only": entry["doc_only"],
            "folder": entry["folder"],
            "has_screenshot": entry["has_screenshot"],
            "has_workflow": entry["has_workflow"],
        })
        if cid.startswith("P") and entry["has_readme"]:
            base["used_by_readme"] = readme_used_by(entry["path"] / "README.md")
        items[cid] = base
    return items


def readme_used_by(readme: Path) -> set[str]:
    """Ids listed under '## Used by' in a pattern README (for the mismatch warning)."""
    text = validate.read_text(readme)
    m = re.search(r"^##\s+Used by\b[^\n]*\n(.*?)(?=^##\s|\Z)", text, re.M | re.S | re.I)
    if not m:
        return set()
    return set(USED_BY_ID_RE.findall(m.group(1)))


def used_by(items: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {pid: [] for pid in items if pid.startswith("P")}
    for cid, it in items.items():
        if cid.startswith("P"):
            continue
        for pid in it["patterns"]:
            out.setdefault(pid, []).append(cid)
    return {k: sorted(v) for k, v in out.items()}


# --- rendering --------------------------------------------------------------------------------------------------

def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _link(title: str, folder: str | None) -> str:
    return f"[{_cell(title)}]({folder}/)" if folder else _cell(title)


def _status(it: dict[str, Any]) -> str:
    label = STATUS_LABEL.get(it["status"], STATUS_LABEL["planned"])
    return f"{label} (docs)" if it["doc_only"] and it["status"] == "shipped" else label


def render_matrix(items: dict[str, dict[str, Any]]) -> str:
    lines: list[str] = []
    for cat in CATEGORY_ORDER:
        rows = sorted((it for it in items.values() if it["category"] == cat and not it["id"].startswith("P")),
                      key=lambda it: it["id"])
        if not rows:
            continue
        shipped = sum(1 for it in rows if it["status"] == "shipped")
        lines.append(f"### {cat} ({shipped}/{len(rows)} shipped)")
        lines.append("")
        lines.append("| ID | Workflow | Difficulty | Patterns | Services | Status |")
        lines.append("|---|---|---|---|---|---|")
        for it in rows:
            pats = ", ".join(_link(pid, items[pid]["folder"]) if pid in items else pid for pid in it["patterns"]) or "-"
            svcs = ", ".join(it["services"]) or "-"
            if it["external"] and it["external"].lower() != "none":
                svcs += f" (optional: {_cell(it['external'])})"
            lines.append(f"| {it['id']} | {_link(it['title'], it['folder'])} | {it['difficulty'] or '-'} | {pats} | "
                         f"{svcs} | {_status(it)} |")
        lines.append("")
    return "\n".join(lines).rstrip("\n")


def render_patterns(items: dict[str, dict[str, Any]]) -> str:
    usage = used_by(items)
    lines = ["| ID | Pattern | Used by | Status |", "|---|---|---|---|"]
    for pid in sorted(i for i in items if i.startswith("P")):
        it = items[pid]
        users = ", ".join(_link(w, items[w]["folder"]) if w in items else w for w in usage.get(pid, [])) or "-"
        lines.append(f"| {pid} | {_link(it['title'], it['folder'])} | {users} | {_status(it)} |")
    return "\n".join(lines)


def counts(items: dict[str, dict[str, Any]]) -> dict[str, int]:
    wf = [it for it in items.values() if not it["id"].startswith("P")]
    pt = [it for it in items.values() if it["id"].startswith("P")]
    return {
        "workflows_total": len(wf), "workflows_shipped": sum(it["status"] == "shipped" for it in wf),
        "workflows_building": sum(it["status"] == "in-progress" for it in wf),
        "patterns_total": len(pt), "patterns_shipped": sum(it["status"] == "shipped" for it in pt),
        "patterns_building": sum(it["status"] == "in-progress" for it in pt),
    }


def render_stats(items: dict[str, dict[str, Any]]) -> str:
    c = counts(items)
    extra = f" ({c['workflows_building'] + c['patterns_building']} building)" \
        if c["workflows_building"] + c["patterns_building"] else ""
    return (f"**{c['workflows_shipped']} of {c['workflows_total']} workflows shipped \u00b7 "
            f"{c['patterns_shipped']} of {c['patterns_total']} patterns**{extra}")


def pattern_warnings(items: dict[str, dict[str, Any]]) -> list[str]:
    """'Used by' in a pattern README vs the workflows' `patterns:` front-matter."""
    warnings: list[str] = []
    usage = used_by(items)
    for pid, it in sorted(items.items()):
        if not pid.startswith("P") or it["used_by_readme"] is None:
            continue
        computed = set(usage.get(pid, []))
        listed = it["used_by_readme"]
        missing = sorted(computed - listed)
        extra = sorted(listed - computed)
        if missing:
            warnings.append(f"{it['folder']}/README.md 'Used by' does not list {', '.join(missing)} "
                            f"(their front-matter references {pid})")
        if extra:
            warnings.append(f"{it['folder']}/README.md 'Used by' lists {', '.join(extra)} "
                            f"but their front-matter does not reference {pid}")
    for pid, users in usage.items():
        if pid not in items:
            warnings.append(f"workflows reference unknown pattern {pid}: {', '.join(users)}")
    return warnings


# --- README block replacement -----------------------------------------------------------------------------------

def replace_block(text: str, marker: str, body: str, *, inline: bool = False) -> tuple[str, bool]:
    """Replace what is between <!-- MARKER:START --> and <!-- MARKER:END -->. Returns (text, found)."""
    start, end = f"<!-- {marker}:START -->", f"<!-- {marker}:END -->"
    pat = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    if not pat.search(text):
        return text, False
    inner = body if inline else f"\n{body}\n"
    return pat.sub(lambda _m: f"{start}{inner}{end}", text, count=1), True


def apply(readme_text: str, items: dict[str, dict[str, Any]]) -> tuple[str, list[str]]:
    missing: list[str] = []
    text, ok = replace_block(readme_text, MARKERS["matrix"], render_matrix(items))
    if not ok:
        missing.append(MARKERS["matrix"])
    text, ok = replace_block(text, MARKERS["patterns"], render_patterns(items))
    if not ok:
        missing.append(MARKERS["patterns"])
    text, ok = replace_block(text, MARKERS["stats"], render_stats(items), inline=True)
    if not ok:
        missing.append(MARKERS["stats"])
    return text, missing


def render_all(items: dict[str, dict[str, Any]]) -> str:
    return (f"{render_stats(items)}\n\n## Coverage matrix\n\n{render_matrix(items)}\n\n"
            f"## Patterns\n\n{render_patterns(items)}\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="repo root (default: parent of scripts/)")
    ap.add_argument("--readme", default=None, help="README to rewrite (default: <root>/README.md)")
    ap.add_argument("--check", action="store_true", help="exit 1 if the README would change (CI)")
    ap.add_argument("--stdout", action="store_true", help="print the rendered tables instead of touching the README")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    root = Path(args.root).resolve()
    readme = Path(args.readme).resolve() if args.readme else root / "README.md"
    items = build_catalog(root)
    for w in pattern_warnings(items):
        print(f"warning: {w}", file=sys.stderr)
    c = counts(items)

    if args.stdout or not readme.exists():
        if not readme.exists():
            print(f"note: {readme} does not exist yet - printing the tables instead", file=sys.stderr)
        print(render_all(items))
        return 0

    original = validate.read_text(readme)
    updated, missing = apply(original, items)
    for m in missing:
        print(f"warning: {readme.name} has no <!-- {m}:START --> / <!-- {m}:END --> markers; block skipped",
              file=sys.stderr)
    changed = updated != original
    summary = (f"{c['workflows_shipped']}/{c['workflows_total']} workflows shipped, "
               f"{c['patterns_shipped']}/{c['patterns_total']} patterns shipped, "
               f"{c['workflows_building'] + c['patterns_building']} building")
    if args.check:
        if changed:
            print(f"FAIL: {validate.rel(readme, root)} is out of date - run `python scripts/build-matrix.py` ({summary})")
            return 1
        print(f"OK: {validate.rel(readme, root)} matrix is up to date ({summary})")
        return 0
    if changed:
        readme.write_text(updated, encoding="utf-8", newline="\n")
        print(f"updated {validate.rel(readme, root)} ({summary})")
    else:
        print(f"{validate.rel(readme, root)} unchanged ({summary})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
