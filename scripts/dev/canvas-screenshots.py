#!/usr/bin/env python
"""canvas-screenshots.py [folder ...] [--width 1600] [--scale 2] - real n8n canvas captures, headless.

Logs in to the running n8n with the owner from .env (never printed), opens each workflow's editor in headless
Chromium (Playwright), zooms to fit, hides the editor chrome and writes assets/screenshot.png for every folder
given (default: every workflows/ and patterns/ folder whose workflow.json id exists in n8n).

    python scripts/dev/canvas-screenshots.py                          # all folders
    python scripts/dev/canvas-screenshots.py workflows/M05-exchange-rate-watcher

Requires: stack up, `bash scripts/setup.sh` run once, `pip install playwright && playwright install chromium`.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _n8n import REPO, base_url, read_env, workflow_folders  # noqa: E402

HIDE_CSS = """
#side-menu, [data-test-id="main-sidebar"], [data-test-id="canvas-controls"], .vue-flow__minimap,
[data-test-id="node-creator-plus-button"], [data-test-id="canvas-plus-button"], [data-test-id="canvas-node-creator-button"],
.el-notification, .el-dialog__wrapper, [data-test-id="ask-assistant-floating-button"],
[data-test-id="execute-workflow-button"], [data-test-id="canvas-tabs"], .el-radio-group,
[data-test-id="node-view-root"] > div > div:has(> button), [data-test-id="right-sidebar"],
[data-test-id="workflow-logs-panel"], [data-test-id="canvas-controls-right"] { display: none !important; }
"""

# Bounding box (CSS px) of every canvas node incl. sticky notes, padded; None when nothing rendered.
BBOX_JS = """
() => {
  const els = [...document.querySelectorAll('.vue-flow__node')];
  if (!els.length) return null;
  let l = 1e9, t = 1e9, r = -1e9, b = -1e9;
  for (const e of els) { const q = e.getBoundingClientRect(); l = Math.min(l, q.left); t = Math.min(t, q.top); r = Math.max(r, q.right); b = Math.max(b, q.bottom); }
  // also hide anything that looks like a floating toolbar (buttons outside the canvas nodes)
  for (const btn of document.querySelectorAll('button')) {
    const txt = (btn.textContent || '').trim();
    if (/^(Execute workflow|Test workflow|Editor|Executions|Evaluations)/.test(txt)) { const p = btn.closest('div'); (p || btn).style.display = 'none'; }
  }
  return { l, t, r, b };
}
"""


def stable_box(page, tries: int = 12) -> dict | None:
    """Bounding box once two consecutive readings agree (zoom/fit animations settle)."""
    prev = page.evaluate(BBOX_JS)
    for _ in range(tries):
        time.sleep(0.3)
        cur = page.evaluate(BBOX_JS)
        if cur == prev:
            return cur
        prev = cur
    return prev


def capture(page, folder: Path, base: str, width: int) -> str:
    wf = json.loads((folder / "workflow.json").read_text(encoding="utf-8"))
    page.goto(f"{base}/workflow/{wf['id']}", wait_until="networkidle")
    page.wait_for_selector('[data-test-id="canvas-node"]', timeout=30000)
    page.keyboard.press("Escape")            # dismiss any first-visit dialog
    page.add_style_tag(content=HIDE_CSS)
    time.sleep(0.8)
    vw, vh = page.viewport_size["width"], page.viewport_size["height"]
    page.mouse.click(8, vh - 8)              # bottom-left corner: empty canvas once the sidebar is hidden
    page.keyboard.press("1")                 # zoom to fit
    pad = 60                                 # room for labels under nodes and the output stubs on the right
    box = stable_box(page)
    for _ in range(6):                       # fit-to-view ignores the hidden chrome; zoom out until it really fits
        if not box or (box["l"] >= pad and box["t"] >= pad and box["r"] <= vw - pad and box["b"] <= vh - pad):
            break
        page.keyboard.press("-")
        box = stable_box(page)
    page.keyboard.press("Escape")            # deselect
    page.mouse.move(vw - 4, vh - 4)          # no hover toolbar on a node
    box = stable_box(page)
    out = folder / "assets" / "screenshot.png"
    out.parent.mkdir(exist_ok=True)
    if box:
        clip = {"x": max(0, box["l"] - pad), "y": max(0, box["t"] - pad // 2)}   # less on top: nothing renders above
        clip["width"] = min(vw, box["r"] + pad) - clip["x"]
        clip["height"] = min(vh, box["b"] + pad) - clip["y"]
        page.screenshot(path=str(out), type="png", clip=clip)
    else:
        page.screenshot(path=str(out), type="png")
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return "saved (install Pillow to verify the size)"
    with Image.open(out) as im:
        w, h = im.size
        if im.mode != "RGB":
            im.convert("RGB").save(out)
    if w < 1200:
        raise RuntimeError(f"capture only {w}px wide (< 1200); widen --width or check the canvas")
    return f"{w}x{h}"


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folders", nargs="*", help="workflow/pattern folders (default: all with a workflow.json)")
    ap.add_argument("--width", type=int, default=1600, help="viewport width in CSS px (default 1600)")
    ap.add_argument("--scale", type=int, default=2, help="device scale factor (default 2 -> ~3000 px captures)")
    ns = ap.parse_args()
    width, scale = ns.width, ns.scale
    folders = [Path(a) for a in ns.folders] or workflow_folders()
    env = read_env()
    base = base_url(env)
    email = env.get("N8N_OWNER_EMAIL", "owner@lab.local")
    password = env.get("N8N_OWNER_PASSWORD", "LabOwner2026x")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("pip install playwright && playwright install chromium")
        return 1
    failed = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": width, "height": 1000}, device_scale_factor=scale)
        r = ctx.request.post(f"{base}/rest/login", data={"emailOrLdapLoginId": email, "email": email, "password": password})
        if r.status != 200:
            print(f"login failed: HTTP {r.status} (owner from .env; run bash scripts/setup.sh first)")
            return 1
        page = ctx.new_page()
        for folder in folders:
            if not (folder / "workflow.json").exists():
                continue
            try:
                size = capture(page, folder, base, width)
                print(f"{folder.as_posix()}: assets/screenshot.png {size}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"{folder.as_posix()}: FAILED {type(exc).__name__}: {str(exc).splitlines()[0][:120]}")
        browser.close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
