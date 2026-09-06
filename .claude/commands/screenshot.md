---
description: Refresh assets/screenshot.png for one or all items - a real n8n canvas capture via Chrome when the stack is running, otherwise the auto-rendered preview.
argument-hint: <ID|all> [--preview-only]
allowed-tools: Bash(python:*), Bash(curl:*), Bash(bash scripts/*), Read, ToolSearch, mcp__claude-in-chrome__tabs_context_mcp, mcp__claude-in-chrome__tabs_create_mcp, mcp__claude-in-chrome__navigate, mcp__claude-in-chrome__computer, mcp__claude-in-chrome__read_page, mcp__claude-in-chrome__javascript_tool, mcp__claude-in-chrome__tabs_close_mcp
---

Target: `$ARGUMENTS` (a catalog ID or `all`).

1. If `--preview-only` or `curl -s http://localhost:5678/healthz` fails: run `python scripts/render-preview.py <folder...>`
   and stop (the preview is a faithful node graph, labelled as auto-rendered).
2. Otherwise load the Chrome tools with a single ToolSearch (tabs_context, tabs_create, navigate, computer, read_page,
   javascript_tool, tabs_close). Make sure the item is imported (`bash scripts/import-workflows.sh <folder>`), open
   `http://localhost:5678/workflow/<workflow id>` in a new tab (log in with the owner from `.env` if prompted - the user
   has to type the password; never read `.env` yourself), wait for the canvas, press `1` to zoom-to-fit, hide the
   left sidebar if it overlaps, and capture the canvas at >= 1400 px width. Save via
   `python scripts/dev/save-screenshot.py <folder> <path-to-capture>`; it crops and verifies the width.
3. `python scripts/validate.py <folder>` to confirm the asset passes, and close the tabs you opened.
