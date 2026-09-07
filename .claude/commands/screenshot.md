---
description: Refresh assets/screenshot.png for one or all items - a real n8n canvas capture (headless Chromium) when the stack is running, otherwise the auto-rendered preview.
argument-hint: <ID|all> [--preview-only]
allowed-tools: Bash(python:*), Bash(curl:*), Bash(bash scripts/*), Read
---

Target: `$ARGUMENTS` (a catalog ID, a folder, or `all`).

1. If `--preview-only` or `curl -s http://localhost:5678/healthz` fails: run `python scripts/render-preview.py <folder...>`
   and stop (the preview is a faithful node graph, labelled as auto-rendered).
2. Otherwise make sure the item is imported (`bash scripts/import-workflows.sh <folder>`), then run
   `python scripts/dev/canvas-screenshots.py <folder...>` (no argument = every folder). It logs in with the owner from
   `.env` (the script reads it; never print it), opens each editor in headless Chromium, zooms to fit, hides the editor
   chrome, crops to the node bounding box and writes `assets/screenshot.png` (>= 1200 px wide). Needs
   `pip install playwright && playwright install chromium` once.
3. Open one or two of the PNGs with Read to eyeball them (no clipped nodes, no selected node, sticky notes visible),
   then `python scripts/validate.py <folder>` to confirm the asset passes.
