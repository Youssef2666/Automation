#!/usr/bin/env python
"""changelog.py - CHANGELOG-style markdown from conventional commits (used by the release workflow).

  python scripts/changelog.py                      # commits since the latest tag (or the whole history)
  python scripts/changelog.py --since v0.1.0       # commits after a tag / ref
  python scripts/changelog.py --version v0.2.0     # heading text (default: Unreleased)

Groups `type(scope)!: subject` commits into Features (feat), Bug fixes (fix), Documentation (docs), CI (ci),
Chores (chore) and Other (refactor, test, perf, build, style, revert, non-conventional). Commits marked with `!`
or a `BREAKING CHANGE:` footer are listed first under Breaking changes. Merge commits are skipped.
Prints to stdout; standard library only (needs git on PATH).
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = SCRIPT_DIR.parent

CONVENTIONAL_RE = re.compile(r"^(?P<type>[a-zA-Z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:\s*(?P<subject>.+)$")
GROUPS: list[tuple[str, str]] = [
    ("feat", "Features"), ("fix", "Bug fixes"), ("docs", "Documentation"), ("ci", "CI"), ("chore", "Chores"),
    ("other", "Other"),
]
KNOWN_TYPES = {"feat", "fix", "docs", "ci", "chore", "refactor", "test", "perf", "build", "style", "revert"}
FIELD_SEP, RECORD_SEP = "\x1f", "\x1e"


def git(root: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or f"git {' '.join(args)} failed")
    return res.stdout


def latest_tag(root: Path) -> str | None:
    try:
        return git(root, "describe", "--tags", "--abbrev=0").strip() or None
    except RuntimeError:
        return None


def repo_url(root: Path) -> str | None:
    try:
        url = git(root, "remote", "get-url", "origin").strip()
    except RuntimeError:
        return None
    m = re.match(r"^(?:git@|https?://)([^:/]+)[:/](.+?)(?:\.git)?/?$", url)
    if not m:
        return None
    return f"https://{m.group(1)}/{m.group(2)}"


def parse_commit(sha: str, subject: str, body: str) -> dict[str, Any]:
    subject = subject.strip()
    m = CONVENTIONAL_RE.match(subject)
    breaking = bool(re.search(r"^BREAKING[ -]CHANGE:", body, re.M))
    if m and m.group("type").lower() in KNOWN_TYPES:
        ctype = m.group("type").lower()
        return {"sha": sha, "type": ctype, "scope": (m.group("scope") or "").strip(),
                "subject": m.group("subject").strip(), "breaking": breaking or bool(m.group("bang")),
                "group": ctype if ctype in dict(GROUPS) else "other"}
    return {"sha": sha, "type": "", "scope": "", "subject": subject, "breaking": breaking, "group": "other"}


def read_commits(root: Path, since: str | None) -> list[dict[str, Any]]:
    rng = f"{since}..HEAD" if since else "HEAD"
    fmt = f"%H{FIELD_SEP}%s{FIELD_SEP}%b{RECORD_SEP}"
    out = git(root, "log", "--no-merges", f"--pretty=format:{fmt}", rng)
    commits: list[dict[str, Any]] = []
    for rec in out.split(RECORD_SEP):
        rec = rec.strip("\r\n")
        if not rec.strip():
            continue
        parts = rec.split(FIELD_SEP)
        if len(parts) < 2:
            continue
        sha, subject = parts[0].strip(), parts[1]
        body = parts[2] if len(parts) > 2 else ""
        commits.append(parse_commit(sha, subject, body))
    return commits


def render(commits: list[dict[str, Any]], *, version: str, since: str | None, url: str | None,
           date: str | None = None) -> str:
    date = date or dt.date.today().isoformat()
    lines = [f"## {version} ({date})", ""]
    if since:
        compare = f"[{since}...{version}]({url}/compare/{since}...{'HEAD' if version == 'Unreleased' else version})" \
            if url else f"{since}..{version}"
        lines.append(f"Changes since {since}: {compare}")
        lines.append("")
    if not commits:
        lines.append("_No changes._")
        return "\n".join(lines) + "\n"

    def fmt(c: dict[str, Any]) -> str:
        short = c["sha"][:7]
        link = f"[{short}]({url}/commit/{c['sha']})" if url else f"`{short}`"
        scope = f"**{c['scope']}:** " if c["scope"] else ""
        return f"- {scope}{c['subject']} ({link})"

    breaking = [c for c in commits if c["breaking"]]
    if breaking:
        lines.append("### Breaking changes")
        lines.append("")
        lines.extend(fmt(c) for c in breaking)
        lines.append("")
    for key, title in GROUPS:
        group = [c for c in commits if c["group"] == key]
        if not group:
            continue
        lines.append(f"### {title}")
        lines.append("")
        lines.extend(fmt(c) for c in group)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default=None, help="tag/ref to start after (default: latest tag, else full history)")
    ap.add_argument("--version", default="Unreleased", help="heading for this release (default: Unreleased)")
    ap.add_argument("--no-links", action="store_true", help="do not link commits to the origin remote")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="repo root (default: parent of scripts/)")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    root = Path(args.root).resolve()
    since = args.since or latest_tag(root)
    try:
        commits = read_commits(root, since)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    url = None if args.no_links else repo_url(root)
    sys.stdout.write(render(commits, version=args.version, since=since, url=url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
