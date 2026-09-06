"""Stop hook: self-correction loop.

When Claude wants to finish a turn, run the repo validator. If it reports errors and this is the
first stop attempt of the turn (stop_hook_active == False), block the stop and hand the errors back
so Claude fixes them before declaring work done. Never blocks twice in a row, so it cannot loop.
"""
from __future__ import annotations

import json
import subprocess
import sys

from _common import PROJECT_DIR, read_payload


def main() -> None:
    p = read_payload()
    if p.get("stop_hook_active"):
        sys.exit(0)
    validator = PROJECT_DIR / "scripts" / "validate.py"
    if not validator.exists():
        sys.exit(0)
    try:
        res = subprocess.run([sys.executable, str(validator), "--json", "--quiet"], cwd=PROJECT_DIR,
                             capture_output=True, text=True, timeout=120)
    except Exception:
        sys.exit(0)
    try:
        report = json.loads(res.stdout or "{}")
    except Exception:
        sys.exit(0)
    errors = report.get("errors", [])
    if not errors:
        sys.exit(0)
    top = "\n".join(f"  - {e}" for e in errors[:12])
    more = f"\n  ... and {len(errors) - 12} more" if len(errors) > 12 else ""
    print(json.dumps({
        "decision": "block",
        "reason": f"scripts/validate.py reports {len(errors)} error(s). Fix them (or mark the item status: in-progress) before stopping:\n{top}{more}",
    }))
    sys.exit(0)


if __name__ == "__main__":
    main()