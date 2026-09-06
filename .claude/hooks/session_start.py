"""SessionStart hook: print a compact project status so every session starts oriented.

Everything printed to stdout becomes context for Claude.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

from _common import PROJECT_DIR


def sh(cmd: list[str], timeout: int = 5) -> str:
    try:
        return subprocess.run(cmd, cwd=PROJECT_DIR, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception:
        return ""


def front_matter(readme: Path) -> dict:
    try:
        text = readme.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm


def catalog() -> str:
    rows = defaultdict(Counter)
    for base in ("workflows", "patterns"):
        for readme in sorted((PROJECT_DIR / base).glob("*/README.md")):
            fm = front_matter(readme)
            cat = fm.get("category", base)
            rows[cat][fm.get("status", "unknown")] += 1
    if not rows:
        return "catalog: no workflow folders yet"
    parts = []
    for cat, c in rows.items():
        parts.append(f"{cat}: {c.get('shipped', 0)} shipped / {c.get('in-progress', 0)} wip / {c.get('planned', 0)} planned")
    return "catalog: " + "; ".join(parts)


def main() -> None:
    lines = ["[Automation Lab] session start"]
    branch = sh(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    dirty = sh(["git", "status", "--porcelain"])
    if branch:
        lines.append(f"git: branch={branch} dirty_files={len(dirty.splitlines()) if dirty else 0}")
    else:
        lines.append("git: not a repository yet (run `git init`)")
    docker = sh(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=6)
    lines.append(f"docker: {'daemon ' + docker if docker else 'daemon not running (docker compose config still works)'}")
    if docker:
        ps = sh(["docker", "compose", "-f", "docker/docker-compose.yml", "ps", "--format", "{{.Service}}={{.State}}"], timeout=10)
        if ps:
            lines.append("stack: " + ", ".join(ps.splitlines()))
    lines.append(catalog())
    env = PROJECT_DIR / ".env"
    lines.append(f".env: {'present' if env.exists() else 'missing (copy .env.example)'}")
    lines.append("reminders: never write .env; workflow.json credentials by id/name only; run `python scripts/validate.py` before declaring anything shipped.")
    print("\n".join(lines))
    sys.exit(0)


if __name__ == "__main__":
    main()