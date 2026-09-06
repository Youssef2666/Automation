---
description: Scaffold a new patterns/Pxx folder from the pattern template and the authoring stub.
argument-hint: <PNN> <kebab-slug> "<Title>"
allowed-tools: Bash(python:*), Read, Write, Glob
---

Scaffold pattern **$1** as `patterns/$1-$2/` titled "$3" with `python scripts/scaffold.py $1 $2 "$3" Patterns Intermediate`.
Then read `.claude/skills/pattern-authoring/SKILL.md` and the PRD row (docs/PRD.md section 9.8) and implement the
pattern (failure story, rule, sub-workflow if it ships one, tests, "Used by") - or delegate to the `pattern-author` agent.
