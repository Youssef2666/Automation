"""PreToolUse hook (Write|Edit|MultiEdit): never let secrets or runtime data reach the repo.

Blocks when:
  * the target is a real .env file (only humans edit those),
  * the target is under data/ (runtime volumes) or .git/,
  * the new content contains a credential-looking string,
  * a workflow.json carries credential *values* or non-empty pinData.
"""
from __future__ import annotations

import json
import sys

from _common import deny, is_env_file, read_payload, rel, scan_secrets


def new_text(tool: str, ti: dict) -> str:
    if tool == "Write":
        return ti.get("content", "") or ""
    if tool == "Edit":
        return ti.get("new_string", "") or ""
    if tool == "MultiEdit":
        return "\n".join((e.get("new_string") or "") for e in ti.get("edits", []))
    return ""


def check_workflow_json(tool: str, text: str, path: str) -> list[str]:
    problems: list[str] = []
    if tool != "Write" or not path.replace("\\", "/").endswith("workflow.json"):
        return problems
    try:
        data = json.loads(text)
    except Exception:
        return problems  # post-write hook reports JSON errors with better context
    for node in data.get("nodes", []):
        creds = node.get("credentials") or {}
        for ctype, cval in creds.items():
            if isinstance(cval, dict) and set(cval) - {"id", "name"}:
                problems.append(f"node '{node.get('name')}' credential '{ctype}' has keys other than id/name")
    if data.get("pinData"):
        problems.append("pinData must be empty in committed workflows (move samples to test/)")
    return problems


def main() -> None:
    p = read_payload()
    tool = p.get("tool_name", "")
    ti = p.get("tool_input", {}) or {}
    path = ti.get("file_path", "") or ""
    r = rel(path)

    if is_env_file(path):
        deny(f"Refusing to write '{r}': real .env files are edited by humans only. Update .env.example instead.")
    if r.startswith("data/") or r.startswith(".git/"):
        deny(f"Refusing to write '{r}': data/ holds runtime volumes and .git/ is managed by git.")

    text = new_text(tool, ti)
    findings = scan_secrets(text, path)
    if findings:
        deny(
            "Secret-looking content blocked in '" + r + "':\n  - " + "\n  - ".join(findings[:8])
            + "\nUse placeholders (e.g. 'changeme', '<generated>') or reference credentials by name/id only."
        )
    problems = check_workflow_json(tool, text, path)
    if problems:
        deny("workflow.json contract violation in '" + r + "':\n  - " + "\n  - ".join(problems))
    sys.exit(0)


if __name__ == "__main__":
    main()