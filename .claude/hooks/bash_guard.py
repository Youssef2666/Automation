"""PreToolUse hook (Bash): block irreversible or secret-leaking shell commands.

This is a guard rail, not a sandbox: it catches the common footguns in this repo
(force pushes, wiping docker volumes, dumping decrypted credentials, reading/writing .env).
Some patterns are built from concatenated literals so that the hook file itself never
contains the dangerous command verbatim.
"""
from __future__ import annotations

import re
import subprocess
import sys

from _common import PROJECT_DIR, deny, read_payload

_RECURSIVE_DELETE = r"\b" + "r" + "m" + r"\s+-[a-zA-Z]*r[a-zA-Z]*\s+(/|~|\.|\.\.|\*|data|docker|workflows|patterns|seed|scripts)(\s|/|$)"
_DOCKER_PRUNE = r"\bdocker\s+(system|volume)\s+(prune|r" + "m)\b"

RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bgit\s+push\b.*(\s--force\b|\s-f\b|\s--force-with-lease\b)"), "force pushes are not allowed here; ask the user."),
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "git reset --hard discards work; ask the user first."),
    (re.compile(r"\bgit\s+clean\s+-[a-zA-Z]*f"), "git clean -f deletes untracked files (seed outputs, data/); ask first."),
    (re.compile(r"\bdocker\s+compose\b.*\bdown\b.*(\s-v\b|\s--volumes\b)"), "docker compose down -v wipes the n8n/Postgres volumes; ask the user first."),
    (re.compile(_DOCKER_PRUNE), "docker prune / volume removal is destructive; ask the user first."),
    (re.compile(r"\bexport:credentials\b.*--decrypted"), "never export decrypted credentials; they would land in the repo or the transcript."),
    (re.compile(r"(^|[\s;&|])cat\s+[^|;&]*(^|/|\s)\.env(\s|$)"), "do not print .env (secrets). Read .env.example instead."),
    (re.compile(r"(>|>>|\btee\b)\s*\"?\.?/?\.env(\s|$|\")"), "do not write .env from the shell; humans own that file."),
    (re.compile(_RECURSIVE_DELETE), "recursive delete of a top-level repo folder; ask the user first."),
    (re.compile(r"\bcurl\b[^\n]*\s-d\s+@?\.env\b"), "would post .env to a remote; blocked."),
]


def staged_env_files() -> list[str]:
    try:
        out = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=PROJECT_DIR,
                             capture_output=True, text=True, timeout=5).stdout
        bad = []
        for line in out.splitlines():
            name = line.strip()
            if name == ".env" or (name.startswith(".env.") and not name.endswith(".example")):
                bad.append(name)
        return bad
    except Exception:
        return []


def main() -> None:
    p = read_payload()
    cmd = (p.get("tool_input", {}) or {}).get("command", "") or ""
    flat = " ".join(cmd.split())
    for pat, why in RULES:
        if pat.search(flat):
            deny(f"Blocked command ({why})\n  $ {cmd.strip()[:200]}")
    if re.search(r"\bgit\s+commit\b", flat):
        bad = staged_env_files()
        if bad:
            deny("Refusing to commit: secret files are staged: " + ", ".join(bad) + ". Run `git restore --staged <file>` first.")
    sys.exit(0)


if __name__ == "__main__":
    main()