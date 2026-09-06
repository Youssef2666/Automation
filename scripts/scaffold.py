#!/usr/bin/env python
"""scaffold.py - create a workflow or pattern folder that follows the folder contract, plus its authoring stub.

  python scripts/scaffold.py T01 webhook-to-database --title "Webhook to Database"
  python scripts/scaffold.py P03 idempotency "Idempotency" Patterns Intermediate        # positional form (/new-pattern)
  python scripts/scaffold.py D04 incremental-sync --title "Incremental Sync" --patterns P01,P03 --services core

Creates (refusing to overwrite anything unless --force):
  workflows/<ID>-<slug>/README.md          from .claude/skills/workflow-folder-contract/templates/README.md
     (patterns/ and pattern-README.md for P ids) with __ID__ __TITLE__ __CATEGORY__ __DIFFICULTY__ __FOLDER__ __PATH__ filled
  workflows/<ID>-<slug>/test/README.md     how to replay the sample(s)
  workflows/<ID>-<slug>/test/payload.json  minimal synthetic sample (patterns: input.json + expected.json)
  workflows/<ID>-<slug>/assets/.gitkeep
  .claude/skills/n8n-workflow-json/authoring/<ID>_<slug_with_underscores>.py   minimal n8n_builder stub

Category is derived from the id letter when omitted; difficulty defaults to Beginner (Intermediate for patterns).
--run executes the authoring stub right away so workflow.json exists. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
import validate  # noqa: E402

TEMPLATES = Path(".claude/skills/workflow-folder-contract/templates")
AUTHORING = Path(".claude/skills/n8n-workflow-json/authoring")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Fallbacks if the skill templates are missing (e.g. a trimmed checkout); kept identical to the skill templates.
FALLBACK_WORKFLOW_TEMPLATE = """---
id: __ID__
title: __TITLE__
category: __CATEGORY__
difficulty: __DIFFICULTY__
status: in-progress
patterns: []
services: [core]
tested_on: n8n 2.37.10
---

# __ID__ - __TITLE__

**Category:** __CATEGORY__ · **Difficulty:** __DIFFICULTY__ · **Tested on:** n8n 2.37.10
**Patterns used:** _none yet_

## Problem

One paragraph. What real situation does this solve, and what goes wrong when people do it by hand?

## How it works

1. Trigger: ...
2. Validate / transform: ...
3. Store / notify: ...

![screenshot](assets/screenshot.png)

## Setup

- Services needed: `core` profile (`docker compose --profile core up -d`)
- Credentials: `Postgres - demo` (created by `scripts/setup.sh` from `.env`)
- Import: `bash scripts/import-workflows.sh workflows/__FOLDER__`
- Activate: published automatically by setup when `autopublish: true`; otherwise open the workflow and click **Publish**.

## Try it

```bash
curl -X POST http://localhost:5678/webhook/__PATH__ \\
  -H 'Content-Type: application/json' \\
  -d @workflows/__FOLDER__/test/payload.json
```

Then check: ...

## Notes & trade-offs

- What you would change for production: ...
- What this deliberately does not handle: ...
"""

FALLBACK_PATTERN_TEMPLATE = """---
id: __ID__
title: __TITLE__
category: Patterns
difficulty: Intermediate
status: in-progress
patterns: []
services: [core]
tested_on: n8n 2.37.10
---

# __ID__ - __TITLE__

**Category:** Patterns · **Difficulty:** Intermediate · **Tested on:** n8n 2.37.10

## Problem

What breaks in real deployments when this concern is ignored. Two or three sentences, concrete.

## Pattern

The rule, stated once. Then the decision points (when to apply, when not to).

## Implementation in n8n

1. Node-by-node description of `workflow.json` (if this pattern ships a reusable sub-workflow).
2. How another workflow uses it (Execute Workflow node, settings, expressions).

![screenshot](assets/screenshot.png)

## Trade-offs

- Cost / complexity added
- Failure modes the pattern does not cover

## Used by

- `T01 - Webhook to Database`
- `D04 - Incremental Sync`
"""

AUTHORING_STUB = '''#!/usr/bin/env python
"""{code} - {title}: authoring script (scaffolded by scripts/scaffold.py).

Run:   python {authoring_rel}
Emits: {folder_rel}/workflow.json
Docs:  .claude/skills/n8n-workflow-json/SKILL.md (builder DSL, node allowlist, expressions, verification loop)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from n8n_builder import *  # noqa: E402,F403

wf = Workflow({code!r}, {slug!r}, {title!r}, tags=[{tag!r}]{error_kw})

# TODO: replace the manual trigger with the real trigger and build the flow (see the PRD row for {code}).
start = manual_trigger(wf, "Manual Trigger")
wf.sticky({sticky!r}, pos=(-300, -220))

wf.save()
'''

TEST_README = """# {code} - test inputs

Synthetic samples used by the **Try it** section of `../README.md`. Nothing here is real data
(`@lab.local` / `@example.com` addresses only).

| File | Purpose |
|---|---|
{rows}

Replay: see `../README.md` -> Try it. Keep the sample(s) small, deterministic and free of secrets.
"""


def read_template(root: Path, is_pattern: bool) -> str:
    name = "pattern-README.md" if is_pattern else "README.md"
    path = root / TEMPLATES / name
    if path.exists():
        return validate.read_text(path)
    return FALLBACK_PATTERN_TEMPLATE if is_pattern else FALLBACK_WORKFLOW_TEMPLATE


def fill_readme(template: str, *, code: str, title: str, category: str, difficulty: str, folder: str, path: str,
                patterns: list[str], services: list[str]) -> str:
    text = template
    for key, val in {
        "__ID__": code, "__TITLE__": title, "__CATEGORY__": category, "__DIFFICULTY__": difficulty,
        "__FOLDER__": folder, "__PATH__": path,
    }.items():
        text = text.replace(key, val)
    # pattern template hard-codes Intermediate; honour an explicit difficulty
    if code.startswith("P") and difficulty != "Intermediate":
        text = re.sub(r"^difficulty: Intermediate$", f"difficulty: {difficulty}", text, count=1, flags=re.M)
        text = text.replace("**Difficulty:** Intermediate", f"**Difficulty:** {difficulty}", 1)
    if patterns:
        text = re.sub(r"^patterns: \[\]$", f"patterns: [{', '.join(patterns)}]", text, count=1, flags=re.M)
        text = text.replace("**Patterns used:** _none yet_", "**Patterns used:** " + ", ".join(patterns), 1)
    if services:
        text = re.sub(r"^services: \[[^\]]*\]$", f"services: [{', '.join(services)}]", text, count=1, flags=re.M)
    return text


def sample_files(code: str, title: str, path: str) -> dict[str, str]:
    """Minimal synthetic test inputs (patterns: input + expected; workflows: one webhook-style payload)."""
    if code.startswith("P"):
        inp = {"external_id": f"{code.lower()}-sample-0001", "scope": code.lower(), "note": f"sample input for {title}"}
        exp = {"ok": True, "response": f"{code} sample processed"}
        return {"input.json": json.dumps(inp, indent=2) + "\n", "expected.json": json.dumps(exp, indent=2) + "\n"}
    payload = {
        "source": "automation-lab-sample",
        "event": f"{path}.created",
        "email": "sample.user@lab.local",
        "name": "Sample User",
        "amount": 42.5,
        "note": f"replace with a realistic sample for {code} - {title}",
    }
    return {"payload.json": json.dumps(payload, indent=2) + "\n"}


def scaffold(root: Path, code: str, slug: str, title: str, *, category: str | None = None,
             difficulty: str | None = None, patterns: list[str] | None = None, services: list[str] | None = None,
             force: bool = False, run: bool = False) -> tuple[list[Path], list[str]]:
    """Create everything. Returns (created_paths, errors). Nothing is written when errors are found."""
    errors: list[str] = []
    if not validate.ID_RE.match(code):
        errors.append(f"id '{code}' must look like T01 / D02 / P03 (letters T D M R A B O P + two digits)")
    if not SLUG_RE.match(slug):
        errors.append(f"slug '{slug}' must be kebab-case (lowercase letters, digits, single dashes)")
    if not title.strip():
        errors.append("title is required (--title \"...\" or third positional argument)")
    if errors:
        return [], errors
    is_pattern = code.startswith("P")
    letter_cat = validate.LETTER_TO_CATEGORY[code[0]]
    category = (category or letter_cat).strip()
    if category not in validate.CATEGORIES:
        errors.append(f"category '{category}' not in {sorted(validate.CATEGORIES)}")
    elif category != letter_cat:
        errors.append(f"category '{category}' does not match the id letter {code[0]} ({letter_cat})")
    difficulty = (difficulty or ("Intermediate" if is_pattern else "Beginner")).strip()
    if difficulty not in validate.DIFFICULTIES:
        errors.append(f"difficulty '{difficulty}' not in {'|'.join(validate.DIFFICULTIES)}")
    patterns = [p.strip() for p in (patterns or []) if p.strip()]
    for p in patterns:
        if not validate.PATTERN_ID_RE.match(p):
            errors.append(f"pattern reference '{p}' is not a Pnn id")
    services = [s.strip() for s in (services or []) if s.strip()]
    for s in services:
        if s not in validate.SERVICES:
            errors.append(f"service '{s}' not in {'|'.join(validate.SERVICES)}")
    if errors:
        return [], errors

    folder_name = f"{code}-{slug}"
    base = root / ("patterns" if is_pattern else "workflows")
    folder = base / folder_name
    # a folder with the same id but another slug is a clash too
    if base.is_dir():
        for existing in base.iterdir():
            if existing.is_dir() and existing.name.split("-", 1)[0] == code and existing.name != folder_name:
                errors.append(f"id {code} already exists as {validate.rel(existing, root)}")
    authoring_rel = AUTHORING / f"{code}_{slug.replace('-', '_')}.py"
    files: dict[Path, str] = {
        folder / "README.md": fill_readme(read_template(root, is_pattern), code=code, title=title, category=category,
                                          difficulty=difficulty, folder=folder_name, path=f"{code.lower()}-{slug}",
                                          patterns=patterns, services=services),
        folder / "assets" / ".gitkeep": "",
    }
    samples = sample_files(code, title, f"{code.lower()}-{slug}")
    for name, content in samples.items():
        files[folder / "test" / name] = content
    rows = "\n".join(f"| `{n}` | {'sample input' if 'input' in n or 'payload' in n else 'expected output'} |"
                     for n in samples)
    files[folder / "test" / "README.md"] = TEST_README.format(code=code, rows=rows)
    files[root / authoring_rel] = AUTHORING_STUB.format(
        code=code, slug=slug, title=title, tag=category,
        error_kw="" if code == "P01" else ',\n              error_workflow=catalog_id("P01")',
        authoring_rel=authoring_rel.as_posix(), folder_rel=validate.rel(folder, root),
        sticky=f"## {code} - {title}\nTODO: two or three lines on the flow and the patterns used"
               + (f" ({', '.join(patterns)})." if patterns else "."),
    )
    clashes = [validate.rel(p, root) for p in files if p.exists()]
    if folder.exists() and not force:
        errors.append(f"{validate.rel(folder, root)} already exists - stop, or pass --force to overwrite the scaffold files")
    elif clashes and not force:
        errors.append("refusing to overwrite existing files (use --force): " + ", ".join(clashes))
    if errors:
        return [], errors

    created: list[Path] = []
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        created.append(path)
    if run:
        res = subprocess.run([sys.executable, str(root / authoring_rel)], cwd=root, capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
        if res.returncode != 0:
            errors.append(f"authoring stub failed: {res.stderr.strip() or res.stdout.strip()}")
        else:
            created.append(folder / "workflow.json")
    return created, errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("id", help="catalog id, e.g. T01 or P03")
    ap.add_argument("slug", help="kebab-case slug, e.g. webhook-to-database")
    ap.add_argument("positional", nargs="*", help='optional: "<Title>" [Category] [Difficulty] (slash-command form)')
    ap.add_argument("--title", default=None, help="human title (required unless given positionally)")
    ap.add_argument("--category", default=None, help="Triggers | Data & ETL | ... (derived from the id letter)")
    ap.add_argument("--difficulty", default=None, help="Beginner | Intermediate | Advanced")
    ap.add_argument("--patterns", default="", help="comma-separated pattern ids, e.g. P01,P03")
    ap.add_argument("--services", default="", help="comma-separated compose profiles, e.g. core,ai")
    ap.add_argument("--force", action="store_true", help="overwrite existing scaffold files")
    ap.add_argument("--run", action="store_true", help="run the authoring stub afterwards so workflow.json exists")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="repo root (default: parent of scripts/)")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    pos = list(args.positional)
    title = args.title or (pos.pop(0) if pos else "")
    category = args.category or (pos.pop(0) if pos else None)
    difficulty = args.difficulty or (pos.pop(0) if pos else None)
    if pos:
        print(f"error: unexpected extra arguments: {' '.join(pos)}", file=sys.stderr)
        return 2
    root = Path(args.root).resolve()
    created, errors = scaffold(
        root, args.id.strip().upper(), args.slug.strip(), title.strip(), category=category, difficulty=difficulty,
        patterns=[p for p in re.split(r"[,\s]+", args.patterns) if p], services=[s for s in re.split(r"[,\s]+", args.services) if s],
        force=args.force, run=args.run,
    )
    for e in errors:
        print(f"error: {e}", file=sys.stderr)
    for p in created:
        print(f"created {validate.rel(p, root)}")
    if errors:
        return 1
    folder = validate.rel(created[0].parent, root)
    stub = next((validate.rel(p, root) for p in created if p.suffix == ".py"), "")
    print("\nNext steps:")
    print(f"  1. implement the flow in {stub} and run it (writes {folder}/workflow.json)")
    print(f"  2. replace {folder}/test/* with realistic synthetic samples and write the README sections")
    print(f"  3. python scripts/render-preview.py {folder} && python scripts/validate.py {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
