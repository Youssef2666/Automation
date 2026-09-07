"""PostToolUse hook (Write|Edit|MultiEdit): fast feedback on files that have a contract.

  * workflow.json  -> must parse, have nodes/connections/settings, connections must reference
                      existing node names, credentials only {id,name}; file is re-pretty-printed
                      (2-space indent, trailing newline) so exports stay diff-friendly.
  * README.md under workflows/ or patterns/ -> front-matter keys + id/folder agreement.
  * *.py           -> byte-compile check.
  * docker-compose*.yml -> `docker compose config -q` (no daemon needed).
  * other *.json / *.yml -> parse check.
"""
from __future__ import annotations

import json
import py_compile
import re
import subprocess
import sys
from pathlib import Path

from _common import post_block, post_context, read_payload, rel

REQUIRED_FM = ("id", "title", "category", "difficulty", "status", "patterns", "services")
FOLDER_RE = re.compile(r"^[TDMRABOP]\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*$")


def check_workflow_json(path: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        return [f"invalid JSON: {exc}"], warnings
    for key in ("name", "nodes", "connections", "settings"):
        if key not in data:
            errors.append(f"missing top-level key '{key}'")
    nodes = data.get("nodes", []) or []
    if not nodes:
        errors.append("no nodes")
    names = set()
    for n in nodes:
        for key in ("id", "name", "type", "typeVersion", "position"):
            if key not in n:
                errors.append(f"node '{n.get('name', '?')}' missing '{key}'")
        if n.get("name") in names:
            errors.append(f"duplicate node name '{n.get('name')}'")
        names.add(n.get("name"))
        for ctype, cval in (n.get("credentials") or {}).items():
            if isinstance(cval, dict) and set(cval) - {"id", "name"}:
                errors.append(f"node '{n.get('name')}' credential '{ctype}' must only carry id/name")
    for src, outputs in (data.get("connections") or {}).items():
        if src not in names:
            errors.append(f"connection source '{src}' is not a node")
        for kind, lanes in (outputs or {}).items():
            for lane in lanes or []:
                for edge in lane or []:
                    if edge.get("node") not in names:
                        errors.append(f"connection {src} -> '{edge.get('node')}' targets a missing node")
    if data.get("pinData"):
        errors.append("pinData must be empty")
    if "id" not in data:
        warnings.append("no top-level 'id' (deterministic IDs are required for sub-workflow calls)")
    if not errors:
        pretty = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        if pretty != text:
            path.write_text(pretty, encoding="utf-8", newline="\n")
            warnings.append("re-formatted to canonical 2-space JSON")
    return errors, warnings


def check_readme(path: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return ["README is missing YAML front-matter (--- id/title/category/... ---)"], warnings
    fm = m.group(1)
    keys = {line.split(":", 1)[0].strip() for line in fm.splitlines() if ":" in line and not line.startswith(" ")}
    for k in REQUIRED_FM:
        if k not in keys:
            errors.append(f"front-matter missing '{k}'")
    folder = path.parent.name
    if not FOLDER_RE.match(folder):
        errors.append(f"folder name '{folder}' violates <CATEGORY><NN>-<kebab-name>")
    idm = re.search(r"^id:\s*([A-Z]\d{2})\s*$", fm, re.M)
    if idm and not folder.startswith(idm.group(1) + "-"):
        errors.append(f"front-matter id {idm.group(1)} does not match folder '{folder}'")
    is_pattern = folder.startswith("P")
    required = (("## Problem", "## Pattern", "## Implementation", "## Trade-offs", "## Used by") if is_pattern
                else ("## Problem", "## How it works", "## Setup", "## Try it", "## Notes"))
    for section in required:
        if section not in text:
            warnings.append(f"missing section '{section}'")
    return errors, warnings


def main() -> None:
    p = read_payload()
    ti = p.get("tool_input", {}) or {}
    raw = ti.get("file_path", "") or ""
    if not raw:
        sys.exit(0)
    path = Path(raw)
    if not path.exists():
        sys.exit(0)
    r = rel(path)
    errors: list[str] = []
    warnings: list[str] = []

    if path.name == "workflow.json":
        errors, warnings = check_workflow_json(path)
    elif path.name == "README.md" and (r.startswith("workflows/") or r.startswith("patterns/")) and r.count("/") == 2:
        # only the folder README carries front-matter; test/README.md and friends are plain notes
        errors, warnings = check_readme(path)
    elif path.suffix == ".py":
        try:
            py_compile.compile(str(path), doraise=True)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"does not compile: {exc}")
    elif path.name.startswith("docker-compose") and path.suffix in {".yml", ".yaml"}:
        try:
            res = subprocess.run(["docker", "compose", "-f", str(path), "config", "-q"],
                                 capture_output=True, text=True, timeout=25, cwd=path.parent)
            if res.returncode != 0:
                errors.append("docker compose config failed:\n" + (res.stderr or res.stdout).strip()[:1500])
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"could not run docker compose config: {exc}")
    elif path.suffix == ".json":
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"invalid JSON: {exc}")
    elif path.suffix in {".yml", ".yaml"}:
        try:
            import yaml  # type: ignore

            yaml.safe_load(path.read_text(encoding="utf-8"))
        except ImportError:
            pass
        except Exception as exc:  # noqa: BLE001
            errors.append(f"invalid YAML: {exc}")

    if errors:
        post_block(f"{r}: " + "; ".join(errors[:10]))
    if warnings:
        post_context(f"{r}: " + "; ".join(warnings[:10]))
    sys.exit(0)


if __name__ == "__main__":
    main()