#!/usr/bin/env python
"""validate.py - the single source of truth for the Automation Lab folder contract and secret scan.

Checks (per workflows/<ID>-<slug>/ and patterns/<ID>-<slug>/ folder):
  * folder name, README front-matter (id/title/category/difficulty/status/patterns/services), required sections
  * workflow.json structure (keys, nodes, unique names, connections, credentials by {id,name}, pinData, 16-char id,
    node type/typeVersion allowlist, expression syntax, no $env / Python code / banned nodes)
  * assets/screenshot.png (>= 1200 px wide when shipped), test/ has at least one sample input
  * referenced patterns exist
Repo-wide:
  * secret scan over every text file (patterns mirrored in .claude/hooks/_common.py - keep both in sync)
  * real-looking e-mail addresses in content directories, tracked .env files
  * --docs: relative markdown links / images resolve to existing files

Exit code 1 when there is at least one error (warnings become errors with --strict).
Standard library only; PyYAML is deliberately not used (front-matter is parsed with a tiny reader).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = SCRIPT_DIR.parent

# --- contract constants -------------------------------------------------------------------------
FOLDER_RE = re.compile(r"^[TDMRABOP]\d{2}-[a-z0-9]+(-[a-z0-9]+)*$")
ID_RE = re.compile(r"^[TDMRABOP]\d{2}$")
PATTERN_ID_RE = re.compile(r"^P\d{2}$")
LETTER_TO_CATEGORY = {
    "T": "Triggers", "D": "Data & ETL", "M": "Monitoring", "R": "Documents",
    "A": "AI", "B": "Business", "O": "DevOps", "P": "Patterns",
}
CATEGORY_ORDER = ["Triggers", "Data & ETL", "Monitoring", "Documents", "AI", "Business", "DevOps"]
CATEGORIES = set(CATEGORY_ORDER) | {"Patterns"}
STATUSES = ("planned", "in-progress", "shipped")
STATUS_ICON = {"shipped": "✅ shipped", "in-progress": "\U0001f6a7 in-progress", "planned": "\U0001f4cb planned"}
DIFFICULTIES = ("Beginner", "Intermediate", "Advanced")
SERVICES = ("core", "docs", "ai", "observability")
REQUIRED_FM = ("id", "title", "category", "difficulty", "status", "patterns", "services")
LIST_KEYS = ("patterns", "services", "depends_on", "tags")
DOC_ONLY_IDS: set[str] = set()  # P07 ships a workflow since ADR 0008; doc-only folders declare `doc_only: true` in front-matter
MIN_SCREENSHOT_WIDTH = 1200

# (label, regex) - label is what the error message shows, regex matched per line, case-insensitive.
WORKFLOW_SECTIONS = [
    ("## Problem", r"^##\s+Problem\b"),
    ("## How it works", r"^##\s+How it works\b"),
    ("## Setup", r"^##\s+Setup\b"),
    ("## Try it", r"^##\s+Try it\b"),
    ("## Notes & trade-offs", r"^##\s+Notes\s*(?:&|and)\s*trade-?offs\b"),
]
PATTERN_SECTIONS = [
    ("## Problem", r"^##\s+Problem\b"),
    ("## Pattern", r"^##\s+Pattern\b"),
    ("## Implementation in n8n", r"^##\s+Implementation\b"),
    ("## Trade-offs", r"^##\s+Trade-?offs\b"),
    ("## Used by", r"^##\s+Used by\b"),
]
TEMPLATE_PLACEHOLDER_RE = re.compile(r"__[A-Z]+__")
TEMPLATE_BOILERPLATE = (
    "One paragraph. What real situation does this solve",
    "What breaks in real deployments when this concern is ignored",
    "Then check: ...",
    "- What you would change for production: ...",
)

# --- node allowlist: type -> allowed typeVersions (n8n 2.37, see .claude/skills/n8n-workflow-json/SKILL.md) ----
# Includes the helpers n8n_builder.py emits that the skill table abbreviates (manualTrigger, renameKeys, dateTime,
# retrieverVectorStore). Unknown type = error, known type with another version = warning.
NODE_ALLOWLIST: dict[str, set[float]] = {
    "n8n-nodes-base.webhook": {2},
    "n8n-nodes-base.respondToWebhook": {1.1},
    "n8n-nodes-base.scheduleTrigger": {1.2},
    "n8n-nodes-base.formTrigger": {2.2},
    "n8n-nodes-base.manualTrigger": {1},
    "n8n-nodes-base.emailReadImap": {2},
    "n8n-nodes-base.emailSend": {2.1},
    "n8n-nodes-base.localFileTrigger": {1},
    "n8n-nodes-base.readWriteFile": {1},
    "n8n-nodes-base.telegramTrigger": {1.1},
    "n8n-nodes-base.telegram": {1.2},
    "n8n-nodes-base.postgres": {2.5},
    "n8n-nodes-base.redis": {1},
    "n8n-nodes-base.s3": {1},
    "n8n-nodes-base.httpRequest": {4.2},
    "n8n-nodes-base.code": {2},
    "n8n-nodes-base.set": {3.4},
    "n8n-nodes-base.if": {2.2},
    "n8n-nodes-base.filter": {2.2},
    "n8n-nodes-base.switch": {3.2},
    "n8n-nodes-base.merge": {3},
    "n8n-nodes-base.splitInBatches": {3},
    "n8n-nodes-base.aggregate": {1},
    "n8n-nodes-base.splitOut": {1},
    "n8n-nodes-base.sort": {1},
    "n8n-nodes-base.limit": {1},
    "n8n-nodes-base.removeDuplicates": {1.1, 2},
    "n8n-nodes-base.extractFromFile": {1},
    "n8n-nodes-base.convertToFile": {1.1},
    "n8n-nodes-base.compression": {1.1},
    "n8n-nodes-base.crypto": {1},
    "n8n-nodes-base.html": {1.2},
    "n8n-nodes-base.rssFeedRead": {1.1},
    "n8n-nodes-base.markdown": {1},
    "n8n-nodes-base.xml": {1},
    "n8n-nodes-base.renameKeys": {1},
    "n8n-nodes-base.dateTime": {2},
    "n8n-nodes-base.executeWorkflow": {1.1},
    "n8n-nodes-base.executeWorkflowTrigger": {1.1},
    "n8n-nodes-base.errorTrigger": {1},
    "n8n-nodes-base.stopAndError": {1},
    "n8n-nodes-base.wait": {1.1},
    "n8n-nodes-base.noOp": {1},
    "n8n-nodes-base.n8n": {1},
    "n8n-nodes-base.stickyNote": {1},
    "@n8n/n8n-nodes-langchain.chatTrigger": {1.1},
    "@n8n/n8n-nodes-langchain.agent": {2},
    "@n8n/n8n-nodes-langchain.lmChatOllama": {1},
    "@n8n/n8n-nodes-langchain.embeddingsOllama": {1},
    "@n8n/n8n-nodes-langchain.vectorStoreQdrant": {1},
    "@n8n/n8n-nodes-langchain.retrieverVectorStore": {1},
    "@n8n/n8n-nodes-langchain.documentDefaultDataLoader": {1},
    "@n8n/n8n-nodes-langchain.textSplitterRecursiveCharacterTextSplitter": {1},
    "@n8n/n8n-nodes-langchain.chainLlm": {1.4},
    "@n8n/n8n-nodes-langchain.chainRetrievalQa": {1.4},
    "@n8n/n8n-nodes-langchain.chainSummarization": {2},
    "@n8n/n8n-nodes-langchain.outputParserStructured": {1.2},
    "@n8n/n8n-nodes-langchain.informationExtractor": {1},
    "@n8n/n8n-nodes-langchain.textClassifier": {1},
    "@n8n/n8n-nodes-langchain.toolWorkflow": {1.3},
    "@n8n/n8n-nodes-langchain.toolCode": {1.1},
    "@n8n/n8n-nodes-langchain.memoryBufferWindow": {1.3},
}
BANNED_NODE_TYPES = {
    "n8n-nodes-base.executeCommand": "Execute Command stays disabled in this stack",
    "n8n-nodes-base.function": "deprecated - use the Code node",
    "n8n-nodes-base.functionItem": "deprecated - use the Code node",
    "n8n-nodes-base.spreadsheetFile": "deprecated - use Extract From File / Convert To File",
    "n8n-nodes-base.cron": "deprecated - use Schedule Trigger",
    "n8n-nodes-base.interval": "deprecated - use Schedule Trigger",
    "n8n-nodes-base.start": "deprecated - use Manual Trigger",
}
CONNECTION_KINDS = {"main", "ai_languageModel", "ai_memory", "ai_tool", "ai_outputParser", "ai_embedding",
                    "ai_document", "ai_textSplitter", "ai_vectorStore", "ai_retriever", "ai_reranker"}

# --- secret detection ---------------------------------------------------------------------------
# Copied verbatim from .claude/hooks/_common.py (the Claude Code hook). If you add a rule here, add it there too.
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

EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache", ".mypy_cache",
                ".ruff_cache", ".idea", ".vscode", "dist", "tmp"}
EXCLUDE_TOP_DIRS = {"data"}
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".xlsx", ".xls", ".docx", ".pptx", ".wav",
              ".mp3", ".mp4", ".ttf", ".otf", ".woff", ".woff2", ".zip", ".gz", ".tgz", ".pyc", ".pyo", ".so",
              ".dll", ".exe", ".bin", ".sqlite", ".db"}
MAX_SCAN_BYTES = 4 * 1024 * 1024
LINK_RE = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
FENCE_RE = re.compile(r"^(```|~~~)")


def is_env_file(name: str) -> bool:
    return name == ".env" or (name.startswith(".env.") and name not in {".env.example", ".env.sample", ".env.template"})


# --- small helpers ---------------------------------------------------------------------------------

def rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _scalar(value: str) -> Any:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    low = v.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    return v


def _strip_comment(value: str) -> str:
    v = value.strip()
    if v[:1] in "\"'":
        return v
    return re.split(r"\s+#", v, 1)[0].strip()


def split_front_matter(text: str) -> tuple[str | None, str]:
    """Return (front_matter_block, body) or (None, text)."""
    m = re.match(r"^﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", text, re.S)
    if not m:
        return None, text
    return m.group(1), text[m.end():]


def parse_front_matter(text: str) -> dict[str, Any] | None:
    """Tiny YAML-subset reader: `key: value`, `key: [a, b]`, and block lists (`key:` + `  - item`)."""
    block, _ = split_front_matter(text)
    if block is None:
        return None
    fm: dict[str, Any] = {}
    list_key: str | None = None
    pending_empty: set[str] = set()
    for raw in block.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and list_key is not None:
            fm[list_key].append(_scalar(_strip_comment(stripped[2:])))
            pending_empty.discard(list_key)
            continue
        if line[0] in " \t":
            continue  # nested mappings are not part of the contract
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = _strip_comment(value)
        list_key = None
        if value == "":
            fm[key] = []
            list_key = key
            pending_empty.add(key)
        elif value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            fm[key] = [_scalar(x) for x in inner.split(",") if x.strip()] if inner else []
        else:
            fm[key] = _scalar(value)
    for key in pending_empty:
        if key not in LIST_KEYS:
            fm[key] = ""
    return fm


def wf_id(code: str, slug: str) -> str:
    """Mirror of n8n_builder.wf_id: 16-char deterministic workflow id."""
    camel = "".join(p[:1].upper() + p[1:] for p in re.split(r"[^A-Za-z0-9]+", slug) if p)
    return ("AL" + code + camel)[:16].ljust(16, "0")


def png_size(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as fh:
            head = fh.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def folder_kind(folder: Path) -> str:
    """'workflows' | 'patterns' | '' based on the parent directory name."""
    parent = folder.parent.name
    return parent if parent in ("workflows", "patterns") else ""


def is_doc_only(fm: dict[str, Any] | None, folder_id: str) -> bool:
    if folder_id in DOC_ONLY_IDS:
        return True
    return bool(fm and fm.get("doc_only") is True)


def discover_folders(root: Path, paths: Iterable[str] = ()) -> tuple[list[Path], list[str]]:
    """Resolve CLI paths (or the whole repo) to contract folders. Returns (folders, errors)."""
    errors: list[str] = []
    found: list[Path] = []

    def children(base: Path) -> list[Path]:
        if not base.is_dir():
            return []
        return sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("."))

    paths = list(paths)
    if not paths:
        for base in ("workflows", "patterns"):
            found.extend(children(root / base))
        return found, errors
    for raw in paths:
        p = Path(raw)
        cand = p if p.exists() else root / raw
        if not cand.exists():
            errors.append(f"{raw}: path does not exist")
            continue
        cand = cand.resolve()
        if cand.is_file():
            cand = cand.parent
        if cand == root.resolve():
            for base in ("workflows", "patterns"):
                found.extend(children(root / base))
        elif cand.name in ("workflows", "patterns") and not folder_kind(cand):
            found.extend(children(cand))
        else:
            found.append(cand)
    uniq: list[Path] = []
    seen: set[Path] = set()
    for f in found:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq, errors


def load_catalog(root: Path) -> list[dict[str, Any]]:
    """Every folder's front-matter as a list of dicts (used by build-matrix / milestone)."""
    entries: list[dict[str, Any]] = []
    folders, _ = discover_folders(root)
    for folder in folders:
        readme = folder / "README.md"
        fm = parse_front_matter(read_text(readme)) if readme.exists() else None
        fm = fm or {}
        folder_id = folder.name.split("-", 1)[0]
        entries.append({
            "id": str(fm.get("id") or folder_id),
            "title": str(fm.get("title") or folder.name),
            "category": str(fm.get("category") or LETTER_TO_CATEGORY.get(folder_id[:1], "")),
            "difficulty": str(fm.get("difficulty") or ""),
            "status": str(fm.get("status") or "planned"),
            "patterns": [str(p) for p in (fm.get("patterns") or []) if p] if isinstance(fm.get("patterns"), list) else [],
            "services": [str(s) for s in (fm.get("services") or []) if s] if isinstance(fm.get("services"), list) else [],
            "depends_on": [str(s) for s in (fm.get("depends_on") or [])] if isinstance(fm.get("depends_on"), list) else [],
            "folder": rel(folder, root),
            "path": folder,
            "kind": folder_kind(folder),
            "has_readme": readme.exists(),
            "has_workflow": (folder / "workflow.json").exists(),
            "has_screenshot": (folder / "assets" / "screenshot.png").exists(),
            "doc_only": is_doc_only(fm, folder_id),
            "fm": fm,
        })
    return entries


# --- report ---------------------------------------------------------------------------------------

class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.rows: list[dict[str, Any]] = []

    def error(self, where: str, msg: str) -> None:
        self.errors.append(f"{where}: {msg}")

    def warn(self, where: str, msg: str) -> None:
        self.warnings.append(f"{where}: {msg}")


# --- folder checks -------------------------------------------------------------------------------

def _iter_strings(value: Any, path: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _iter_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _iter_strings(v, f"{path}[{i}]")


def check_workflow_json(path: Path, folder_id: str, folder_slug: str, where: str, rep: Report) -> dict[str, Any] | None:
    try:
        data = json.loads(read_text(path))
    except Exception as exc:  # noqa: BLE001
        rep.error(where, f"workflow.json is not valid JSON ({exc})")
        return None
    if not isinstance(data, dict):
        rep.error(where, "workflow.json must be a JSON object")
        return None
    for key in ("name", "nodes", "connections", "settings"):
        if key not in data:
            rep.error(where, f"workflow.json missing top-level key '{key}'")
    nodes = data.get("nodes") or []
    if not isinstance(nodes, list) or not nodes:
        rep.error(where, "workflow.json has no nodes")
        nodes = []
    names: set[str] = set()
    for n in nodes:
        if not isinstance(n, dict):
            rep.error(where, "workflow.json node entry is not an object")
            continue
        nname = str(n.get("name", "?"))
        for key in ("id", "name", "type", "typeVersion", "position"):
            if key not in n:
                rep.error(where, f"node '{nname}' missing '{key}'")
        if nname in names:
            rep.error(where, f"duplicate node name '{nname}'")
        names.add(nname)
        pos = n.get("position")
        if "position" in n and not (isinstance(pos, list) and len(pos) == 2 and all(isinstance(v, (int, float)) for v in pos)):
            rep.error(where, f"node '{nname}' position must be [x, y]")
        creds = n.get("credentials") or {}
        if creds and not isinstance(creds, dict):
            rep.error(where, f"node '{nname}' credentials must be an object")
        elif creds:
            for ctype, cval in creds.items():
                if not isinstance(cval, dict) or set(cval) - {"id", "name"}:
                    rep.error(where, f"node '{nname}' credential '{ctype}' must only carry id/name")
        ntype = str(n.get("type", ""))
        tver = n.get("typeVersion")
        if ntype in BANNED_NODE_TYPES:
            rep.error(where, f"node '{nname}' uses banned type '{ntype}' ({BANNED_NODE_TYPES[ntype]})")
        elif ntype and ntype not in NODE_ALLOWLIST:
            rep.error(where, f"node '{nname}' type '{ntype}' is not in the allowlist (n8n-workflow-json skill)")
        elif ntype:
            try:
                ok = float(tver) in NODE_ALLOWLIST[ntype]
            except (TypeError, ValueError):
                ok = False
            if not ok:
                allowed = ", ".join(str(v) for v in sorted(NODE_ALLOWLIST[ntype]))
                rep.warn(where, f"node '{nname}' {ntype} typeVersion {tver!r} is not the shipped version ({allowed})")
        params = n.get("parameters") or {}
        if ntype == "n8n-nodes-base.code" and isinstance(params, dict):
            lang = str(params.get("language", "javaScript"))
            if lang.lower().startswith("python") or "pythonCode" in params:
                rep.error(where, f"node '{nname}' is a Python Code node (JavaScript only)")
        if ntype == "n8n-nodes-base.stickyNote":
            continue
        for ppath, s in _iter_strings(params):
            if s.lstrip().startswith("{{"):
                rep.warn(where, f"node '{nname}' parameter {ppath} looks like an expression but has no leading '='")
            if re.search(r"\$env\b", s):
                rep.error(where, f"node '{nname}' parameter {ppath} uses $env (not allowed in shipped workflows)")
            if ntype != "n8n-nodes-base.n8n" and re.search(r"\blocalhost\b|127\.0\.0\.1", s):
                rep.warn(where, f"node '{nname}' parameter {ppath} points at localhost (use compose service names)")
    conns = data.get("connections")
    if conns is not None and not isinstance(conns, dict):
        rep.error(where, "workflow.json 'connections' must be an object")
    for src, outputs in (conns or {}).items() if isinstance(conns, dict) else []:
        if src not in names:
            rep.error(where, f"connection source '{src}' is not a node")
        if not isinstance(outputs, dict):
            rep.error(where, f"connections['{src}'] must be an object of lanes")
            continue
        for kind, lanes in outputs.items():
            if kind not in CONNECTION_KINDS:
                rep.warn(where, f"connection {src}: unknown lane kind '{kind}'")
            for lane in lanes or []:
                for edge in lane or []:
                    target = edge.get("node") if isinstance(edge, dict) else None
                    if target not in names:
                        rep.error(where, f"connection {src} -> '{target}' targets a missing node")
    if data.get("pinData"):
        rep.error(where, "pinData must be empty (samples live in test/)")
    wid = data.get("id")
    if not isinstance(wid, str) or not wid:
        rep.error(where, "workflow.json missing top-level 'id' (deterministic 16-char id from wf_id())")
    elif len(wid) != 16:
        rep.error(where, f"workflow.json id '{wid}' must be exactly 16 characters")
    else:
        expected = wf_id(folder_id, folder_slug)
        if wid != expected:
            rep.warn(where, f"workflow.json id '{wid}' differs from wf_id('{folder_id}', '{folder_slug}') = '{expected}'")
    wname = data.get("name")
    if isinstance(wname, str) and not wname.startswith(folder_id):
        rep.warn(where, f"workflow.json name '{wname}' does not start with '{folder_id}'")
    if data.get("active") is True:
        rep.warn(where, "workflow.json 'active' should be false in exports")
    settings = data.get("settings")
    if isinstance(settings, dict):
        ew = settings.get("errorWorkflow")
        if not ew and folder_id != "P01":
            rep.warn(where, "settings.errorWorkflow is not set (route failures to P01 - error handler)")
        elif ew and (not isinstance(ew, str) or len(ew) != 16):
            rep.warn(where, f"settings.errorWorkflow '{ew}' is not a 16-char workflow id")
    elif settings is not None:
        rep.error(where, "workflow.json 'settings' must be an object")
    return data


def check_folder(folder: Path, root: Path, rep: Report, known_patterns: dict[str, Path],
                 known_ids: dict[str, Path]) -> None:
    where = rel(folder, root)
    row: dict[str, Any] = {"folder": where, "status": "?", "json": "-", "png": "-", "test": "-"}
    rep.rows.append(row)
    err_before, warn_before = len(rep.errors), len(rep.warnings)

    kind = folder_kind(folder)
    if not kind:
        rep.error(where, "folder is not under workflows/ or patterns/")
    if not FOLDER_RE.match(folder.name):
        rep.error(where, "folder name must match <CATEGORY><NN>-<kebab-slug> (e.g. T01-webhook-to-database)")
    folder_id, _, folder_slug = folder.name.partition("-")
    if kind == "patterns" and not folder_id.startswith("P"):
        rep.error(where, "folders under patterns/ must use P ids")
    if kind == "workflows" and folder_id.startswith("P"):
        rep.error(where, "P ids live under patterns/, not workflows/")

    readme = folder / "README.md"
    fm: dict[str, Any] | None = None
    status = "planned"
    if not readme.exists():
        rep.error(where, "README.md is missing")
    else:
        text = read_text(readme)
        fm = parse_front_matter(text)
        if fm is None:
            rep.error(where, "README.md has no YAML front-matter (--- id/title/category/... ---)")
        else:
            for key in REQUIRED_FM:
                if key not in fm:
                    rep.error(where, f"front-matter missing '{key}'")
                elif fm[key] in ("", None):
                    rep.error(where, f"front-matter '{key}' is empty")
            fid = str(fm.get("id", ""))
            if fid and fid != folder_id:
                rep.error(where, f"front-matter id '{fid}' does not match folder prefix '{folder_id}'")
            cat = str(fm.get("category", ""))
            if cat and cat not in CATEGORIES:
                rep.error(where, f"category '{cat}' not in {sorted(CATEGORIES)}")
            elif cat and LETTER_TO_CATEGORY.get(folder_id[:1]) != cat:
                rep.warn(where, f"category '{cat}' does not match the id letter ({folder_id[:1]} = "
                                f"{LETTER_TO_CATEGORY.get(folder_id[:1])})")
            if kind == "patterns" and cat and cat != "Patterns":
                rep.error(where, "patterns/ folders must use category: Patterns")
            status = str(fm.get("status", ""))
            if status not in STATUSES:
                rep.error(where, f"status '{status}' must be one of {'|'.join(STATUSES)}")
                status = "planned"
            diff = str(fm.get("difficulty", ""))
            if diff and diff not in DIFFICULTIES:
                rep.warn(where, f"difficulty '{diff}' not in {'|'.join(DIFFICULTIES)}")
            pats = fm.get("patterns")
            if pats is not None and not isinstance(pats, list):
                rep.error(where, "front-matter 'patterns' must be a list like [P01, P03]")
                pats = []
            for pid in pats or []:
                pid = str(pid)
                if not PATTERN_ID_RE.match(pid):
                    rep.error(where, f"patterns entry '{pid}' is not a pattern id (Pnn)")
                elif pid not in known_patterns:
                    rep.error(where, f"references pattern {pid} but patterns/{pid}-* does not exist")
            if status == "shipped" and kind == "workflows" and not pats:
                rep.warn(where, "shipped workflow references no pattern (acceptance: at least one where applicable)")
            svcs = fm.get("services")
            if svcs is not None and not isinstance(svcs, list):
                rep.error(where, "front-matter 'services' must be a list like [core]")
            for svc in svcs or [] if isinstance(svcs, list) else []:
                if str(svc) not in SERVICES:
                    rep.warn(where, f"services entry '{svc}' not in {'|'.join(SERVICES)}")
            for dep in fm.get("depends_on") or [] if isinstance(fm.get("depends_on"), list) else []:
                if str(dep) not in known_ids:
                    rep.warn(where, f"depends_on '{dep}' has no folder")
            if "tested_on" not in fm:
                rep.warn(where, "front-matter has no 'tested_on' (template: tested_on: n8n 2.37.10)")
            # sections + template leftovers
            sections = PATTERN_SECTIONS if kind == "patterns" else WORKFLOW_SECTIONS
            for label, pattern in sections:
                if not re.search(pattern, text, re.M | re.I):
                    rep.error(where, f"README missing section {label}")
            h1 = re.search(r"^#\s+(.+)$", text, re.M)
            if not h1:
                rep.warn(where, "README has no H1 title (# <ID> - <Title>)")
            elif not h1.group(1).strip().startswith(folder_id):
                rep.warn(where, f"README H1 '{h1.group(1).strip()}' should start with '{folder_id} - '")
            if TEMPLATE_PLACEHOLDER_RE.search(text):
                rep.error(where, f"README still contains template placeholders ({TEMPLATE_PLACEHOLDER_RE.search(text).group(0)})")
            leftovers = [b for b in TEMPLATE_BOILERPLATE if b in text]
            if leftovers:
                msg = f"README still contains template boilerplate ('{leftovers[0][:40]}...')"
                (rep.error if status == "shipped" else rep.warn)(where, msg)
            if "assets/screenshot.png" not in text:
                rep.warn(where, "README does not embed assets/screenshot.png")
    row["status"] = status
    doc_only = is_doc_only(fm, folder_id)

    wf_path = folder / "workflow.json"
    if wf_path.exists():
        data = check_workflow_json(wf_path, folder_id, folder_slug, where, rep)
        row["json"] = "ok" if data is not None and len(rep.errors) == err_before else "ERR"
        if data is not None and row["json"] == "ok":
            row["json"] = f"{len(data.get('nodes') or [])} nodes"
    elif status != "planned" and not doc_only:
        rep.error(where, "workflow.json is missing (required once status is in-progress or shipped)")
    elif doc_only:
        row["json"] = "doc-only"

    shot = folder / "assets" / "screenshot.png"
    if shot.exists():
        size = png_size(shot)
        if size is None:
            try:
                from PIL import Image  # type: ignore

                with Image.open(shot) as im:
                    size = im.size
            except Exception:  # noqa: BLE001
                size = None
        if size is None:
            rep.error(where, "assets/screenshot.png is not a readable PNG")
            row["png"] = "ERR"
        else:
            row["png"] = f"{size[0]}px"
            if size[0] < MIN_SCREENSHOT_WIDTH:
                msg = f"assets/screenshot.png is {size[0]} px wide (minimum {MIN_SCREENSHOT_WIDTH})"
                (rep.error if status == "shipped" else rep.warn)(where, msg)
    else:
        msg = "assets/screenshot.png is missing (python scripts/render-preview.py <folder>)"
        (rep.error if status == "shipped" else rep.warn)(where, msg)

    test_dir = folder / "test"
    samples = [p for p in test_dir.rglob("*") if p.is_file() and p.name not in ("README.md", ".gitkeep")] \
        if test_dir.is_dir() else []
    if samples:
        row["test"] = f"{len(samples)} file{'s' if len(samples) != 1 else ''}"
    else:
        msg = "test/ has no sample input (payload.json, *.csv, *.sql, *.eml ...)"
        if status == "planned" or doc_only:
            rep.warn(where, msg)
        else:
            rep.error(where, msg)
    row["errors"] = len(rep.errors) - err_before
    row["warnings"] = len(rep.warnings) - warn_before


# --- repo-wide checks -----------------------------------------------------------------------------

def iter_text_files(root: Path) -> Iterable[Path]:
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        top = d == root
        dirnames[:] = sorted(
            n for n in dirnames
            if n not in EXCLUDE_DIRS and not (top and n in EXCLUDE_TOP_DIRS) and not (d.name == ".claude" and n == "state")
        )
        for fn in sorted(filenames):
            p = d / fn
            if p.suffix.lower() in BINARY_EXT:
                continue
            if is_env_file(fn):
                continue  # never read real env files; git tracking of them is checked separately
            try:
                if p.stat().st_size > MAX_SCAN_BYTES:
                    continue
            except OSError:
                continue
            yield p


def scan_text(text: str, where: str, content_dir: bool, rep: Report) -> int:
    hits = 0
    for lineno, line in enumerate(text.splitlines(), 1):
        for label, pat in SECRET_PATTERNS:
            for m in pat.finditer(line):
                s = m.group(0)
                rep.error(f"{where}:{lineno}", f"{label}: '{s[:6]}...{s[-4:]}'")
                hits += 1
        if content_dir:
            for m in EMAIL_RE.finditer(line):
                domain = m.group(1).lower()
                if domain not in ALLOWED_EMAIL_DOMAINS and not domain.endswith(".local"):
                    rep.error(f"{where}:{lineno}", f"Real-looking email '{m.group(0)}' (use @lab.local or @example.com)")
                    hits += 1
    return hits


def scan_repo_secrets(root: Path, rep: Report) -> int:
    scanned = 0
    for p in iter_text_files(root):
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:8000]:
            continue
        r = rel(p, root)
        scan_text(raw.decode("utf-8", errors="replace"), r, r.split("/")[0] in CONTENT_DIRS, rep)
        scanned += 1
    return scanned


def check_tracked_env(root: Path, rep: Report) -> None:
    try:
        res = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, timeout=20,
                             encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return
    if res.returncode != 0:
        return
    for line in res.stdout.splitlines():
        name = line.strip().split("/")[-1]
        if is_env_file(name):
            rep.error(line.strip(), "real env file is tracked by git - remove it from the index and rotate its values")
    gi = root / ".gitignore"
    if not gi.exists() or not re.search(r"^\s*\.env\s*$", read_text(gi), re.M):
        rep.warn(".gitignore", "does not ignore .env")


def slugify(heading: str) -> str:
    h = re.sub(r"[`*_~]", "", heading.strip().lower())
    h = re.sub(r"[^\w\- ]", "", h)
    return h.strip().replace(" ", "-")


def check_docs(root: Path, rep: Report) -> int:
    files: list[Path] = []
    top = root / "README.md"
    if top.exists():
        files.append(top)
    for base in ("docs", "workflows", "patterns"):
        d = root / base
        if d.is_dir():
            files.extend(sorted(p for p in d.rglob("*.md") if not any(part in EXCLUDE_DIRS for part in p.parts)))
    checked = 0
    for md in files:
        text = read_text(md)
        where = rel(md, root)
        anchors = {slugify(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.M)}
        in_fence = False
        checked += 1
        for lineno, line in enumerate(text.splitlines(), 1):
            if FENCE_RE.match(line.strip()):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for m in LINK_RE.finditer(line):
                target = m.group(1).strip()
                if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("//"):
                    continue  # http(s):, mailto:, data: ...
                if target.startswith("#"):
                    if slugify(target[1:]) not in anchors and target[1:] not in anchors:
                        rep.warn(f"{where}:{lineno}", f"anchor '{target}' not found in this file")
                    continue
                path_part = target.split("#", 1)[0].split("?", 1)[0]
                if not path_part:
                    continue
                dest = (root / path_part.lstrip("/")) if path_part.startswith("/") else (md.parent / path_part)
                if not dest.exists():
                    rep.error(f"{where}:{lineno}", f"broken link '{target}'")
    return checked


# --- driver -------------------------------------------------------------------------------------------

def run(root: Path, paths: list[str], *, docs: bool = False, secrets_only: bool = False) -> tuple[Report, int]:
    rep = Report()
    checked = 0
    if not secrets_only:
        folders, errs = discover_folders(root, paths)
        for e in errs:
            rep.error("paths", e)
        known_patterns: dict[str, Path] = {}
        known_ids: dict[str, Path] = {}
        for base in ("workflows", "patterns"):
            d = root / base
            if d.is_dir():
                for p in sorted(x for x in d.iterdir() if x.is_dir()):
                    fid = p.name.split("-", 1)[0]
                    if ID_RE.match(fid):
                        known_ids.setdefault(fid, p)
                        if base == "patterns":
                            known_patterns.setdefault(fid, p)
        seen_ids: dict[str, str] = {}
        for folder in folders:
            fid = folder.name.split("-", 1)[0]
            if fid in seen_ids:
                rep.error(rel(folder, root), f"id {fid} is already used by {seen_ids[fid]}")
            seen_ids.setdefault(fid, rel(folder, root))
            check_folder(folder, root, rep, known_patterns, known_ids)
        checked = len(folders)
        if docs:
            check_docs(root, rep)
    scanned = scan_repo_secrets(root, rep)
    check_tracked_env(root, rep)
    rep.scanned = scanned  # type: ignore[attr-defined]
    return rep, checked


def print_human(rep: Report, checked: int, *, quiet: bool, secrets_only: bool) -> None:
    out = sys.stdout
    if not quiet and rep.rows:
        w = max(len("Folder"), *(len(r["folder"]) for r in rep.rows))
        print(f"{'Folder':<{w}}  {'Status':<12} {'JSON':<10} {'PNG':<8} {'Test':<8} {'Err':>3} {'Warn':>4}", file=out)
        for r in rep.rows:
            print(f"{r['folder']:<{w}}  {r['status']:<12} {r['json']:<10} {r['png']:<8} {r['test']:<8} "
                  f"{r.get('errors', 0):>3} {r.get('warnings', 0):>4}", file=out)
        print(file=out)
    if rep.errors:
        print(f"ERRORS ({len(rep.errors)}):", file=out)
        for e in rep.errors:
            print(f"  - {e}", file=out)
    if rep.warnings and not quiet:
        print(f"WARNINGS ({len(rep.warnings)}):", file=out)
        for wmsg in rep.warnings:
            print(f"  - {wmsg}", file=out)
    scanned = getattr(rep, "scanned", 0)
    verdict = "FAIL" if rep.errors else "OK"
    if secrets_only:
        print(f"{verdict}: secret scan over {scanned} files, {len(rep.errors)} errors, {len(rep.warnings)} warnings", file=out)
    else:
        print(f"{verdict}: {checked} folders checked, {len(rep.errors)} errors, {len(rep.warnings)} warnings "
              f"(secret scan: {scanned} files)", file=out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Examples:\n  python scripts/validate.py\n  python scripts/validate.py workflows/T01-webhook-to-database --strict\n"
                                        "  python scripts/validate.py --secrets-only\n  git log --all -p | python scripts/validate.py --secrets-stdin")
    ap.add_argument("paths", nargs="*", help="folders to check (default: every workflows/* and patterns/* folder)")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="repo root (default: parent of scripts/)")
    ap.add_argument("--json", action="store_true", help='print {"errors": [...], "warnings": [...], "checked": n} only')
    ap.add_argument("--quiet", action="store_true", help="no table / warnings, only errors and the final line")
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    ap.add_argument("--secrets-only", action="store_true", help="run only the repo-wide secret scan")
    ap.add_argument("--secrets-stdin", action="store_true", help="scan text from stdin (e.g. git log -p) for secrets")
    ap.add_argument("--docs", action="store_true", help="also check relative markdown links/images in README/docs/workflows/patterns")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"error: root {root} is not a directory", file=sys.stderr)
        return 1

    if args.secrets_stdin:
        rep = Report()
        text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        scan_text(text, "stdin", False, rep)
        if args.json:
            print(json.dumps({"errors": rep.errors, "warnings": rep.warnings, "checked": 0}))
        else:
            for e in rep.errors:
                print(f"  - {e}")
            print(f"{'FAIL' if rep.errors else 'OK'}: stdin scan, {len(rep.errors)} findings")
        return 1 if rep.errors else 0

    rep, checked = run(root, args.paths, docs=args.docs, secrets_only=args.secrets_only)
    if args.strict:
        rep.errors.extend(w for w in rep.warnings)
        rep.warnings = []
    if args.json:
        print(json.dumps({"errors": rep.errors, "warnings": rep.warnings, "checked": checked}, ensure_ascii=False))
    else:
        print_human(rep, checked, quiet=args.quiet, secrets_only=args.secrets_only)
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
