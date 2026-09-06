#!/usr/bin/env python
"""strip-export.py <raw-export-dir> [ID ...] - map raw n8n exports back to repo folders.

For every exported JSON whose top-level `id` matches a folder's workflow.json id (workflows/ or patterns/),
write a cleaned copy over that folder's workflow.json:
  * credentials reduced to {id, name}; pinData -> {}; active -> false
  * meta.instanceId / meta.templateId / versionId / createdAt / updatedAt / shared / homeProject removed
  * staticData dropped; nodes keep their order; canonical 2-space JSON
Exports with no matching folder are listed so you can scaffold a folder or delete them in n8n.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DROP_TOP = {"createdAt", "updatedAt", "shared", "homeProject", "sharedWithProjects", "staticData", "triggerCount",
            "versionId", "activeVersionId", "publishedVersionId", "isArchived", "usedCredentials", "scopes"}


def clean(doc: dict) -> dict:
    for node in doc.get("nodes", []):
        creds = node.get("credentials") or {}
        node["credentials"] = {t: {"id": v.get("id"), "name": v.get("name")} for t, v in creds.items()} if creds else {}
        if not node["credentials"]:
            node.pop("credentials", None)
    doc["pinData"] = {}
    doc["active"] = False
    meta = doc.get("meta") or {}
    meta.pop("instanceId", None)
    meta.pop("templateId", None)
    doc["meta"] = meta or {"templateCredsSetupCompleted": True}
    for k in DROP_TOP:
        doc.pop(k, None)
    if isinstance(doc.get("tags"), list):
        doc["tags"] = [{"name": t.get("name")} for t in doc["tags"] if isinstance(t, dict) and t.get("name")]
    ordered = {k: doc[k] for k in ("name", "nodes", "connections", "active", "settings", "pinData", "meta", "id", "tags") if k in doc}
    ordered.update({k: v for k, v in doc.items() if k not in ordered})
    return ordered


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    raw_dir = REPO / sys.argv[1]
    only = {a.upper() for a in sys.argv[2:]}
    folders: dict[str, Path] = {}
    for base in ("workflows", "patterns"):
        for wf in (REPO / base).glob("*/workflow.json"):
            try:
                folders[json.loads(wf.read_text(encoding="utf-8")).get("id", "")] = wf
            except Exception:  # noqa: BLE001
                pass
    written, unmatched = 0, []
    for f in sorted(raw_dir.glob("*.json")):
        doc = json.loads(f.read_text(encoding="utf-8"))
        target = folders.get(doc.get("id", ""))
        if not target:
            unmatched.append(f"{doc.get('id')}  {doc.get('name')}")
            continue
        code = target.parent.name[:3]
        if only and code not in only:
            continue
        target.write_text(json.dumps(clean(doc), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print(f"updated {target.relative_to(REPO)}")
        written += 1
    print(f"{written} folder(s) updated")
    if unmatched:
        print("no folder for:\n  " + "\n  ".join(unmatched))
    return 0


if __name__ == "__main__":
    sys.exit(main())
