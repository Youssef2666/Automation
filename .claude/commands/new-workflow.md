---
description: Scaffold a new workflow folder (README from template, authoring script stub, test/ and assets/) for a catalog ID.
argument-hint: <ID> <kebab-slug> "<Title>" [Category] [Difficulty]
allowed-tools: Bash(python:*), Read, Write, Glob
---

Scaffold catalog item **$1** as `workflows/$1-$2/` (or `patterns/` when the ID starts with P) titled "$3".

Steps:
1. Run `python scripts/scaffold.py $ARGUMENTS`. It creates the folder, `README.md` from
   `.claude/skills/workflow-folder-contract/templates/`, `test/README.md`, `assets/.gitkeep`, and the authoring stub
   `.claude/skills/n8n-workflow-json/authoring/$1_<slug_with_underscores>.py`.
2. Read the PRD row for $1 in `docs/PRD.md` section 9 and summarise in two lines what the workflow must do and which
   patterns it should reference.
3. Open the authoring stub and the skill `.claude/skills/n8n-workflow-json/SKILL.md`; then implement the workflow
   following the authoring procedure in `.claude/skills/workflow-folder-contract/SKILL.md` (or delegate to the
   `workflow-builder` agent with the ID).

If the folder already exists, stop and say so instead of overwriting.
