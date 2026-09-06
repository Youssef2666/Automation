"""Shared helpers for Automation Lab Claude Code hooks.

Hooks receive a JSON payload on stdin and communicate back via exit codes / JSON on stdout:
  exit 0            -> allow / nothing to say
  exit 2 + stderr   -> block (PreToolUse) or feed the message back to Claude (PostToolUse/Stop)
  JSON on stdout    -> structured decision (see docs.anthropic.com/claude-code/hooks)
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()

# --- secret detection ---------------------------------------------------------------------------
# Kept deliberately in sync with scripts/validate.py (the CI scanner). If you add a rule here,
# add it there too.
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS access key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("OpenAI-style key", re.compile(r"\bsk-(proj-|ant-)?[A-Za-z0-9_-]{20,}\b")),
    ("GitHub token", re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Telegram bot token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("Private key block", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |PGP |DSA )?PRIVATE KEY")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("Stripe key", re.compile(r"\b(sk|rk)_(live|test)_[A-Za-z0-9]{20,}\b")),
    ("SendGrid key", re.compile(r"\bSG\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b")),
]

# Emails are allowed only on clearly-fictional / vendor domains inside content directories.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
ALLOWED_EMAIL_DOMAINS = {
    "lab.local", "example.com", "example.org", "example.net", "n8n.io", "github.com",
    "users.noreply.github.com", "localhost", "anthropic.com",
}
CONTENT_DIRS = ("workflows", "patterns", "seed", "docker", "docs", "scripts", ".github")


def read_payload() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def rel(path: str | Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_DIR)).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def is_env_file(path: str) -> bool:
    name = Path(path).name
    return name == ".env" or (name.startswith(".env.") and name not in {".env.example", ".env.sample", ".env.template"})


def scan_secrets(text: str, path: str = "") -> list[str]:
    """Return human-readable findings for secret-looking strings in `text`."""
    findings: list[str] = []
    for label, pat in SECRET_PATTERNS:
        for m in pat.finditer(text):
            snippet = m.group(0)
            findings.append(f"{label}: '{snippet[:6]}...{snippet[-4:]}'")
    r = rel(path) if path else ""
    if r and r.split("/")[0] in CONTENT_DIRS:
        for m in EMAIL_RE.finditer(text):
            domain = m.group(1).lower()
            if domain not in ALLOWED_EMAIL_DOMAINS and not domain.endswith(".local"):
                findings.append(f"Real-looking email '{m.group(0)}' (use @lab.local or @example.com)")
    return findings


def deny(reason: str) -> None:
    """PreToolUse: block the tool call with a reason Claude can act on."""
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(out))
    sys.exit(0)


def post_block(reason: str) -> None:
    """PostToolUse: tell Claude the result is not acceptable and why."""
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


def post_context(message: str) -> None:
    """PostToolUse: add non-blocking context for Claude."""
    out = {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": message}}
    print(json.dumps(out))
    sys.exit(0)