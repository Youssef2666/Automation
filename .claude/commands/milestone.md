---
description: Report progress against the PRD milestones (M0-M5) and goals (G1-G6) from front-matter and repo state; list the next items to build.
allowed-tools: Bash(python:*), Read, Glob, Grep
---

Run `python scripts/milestone.py` (falls back to reading `workflows/*/README.md` and `patterns/*/README.md`
front-matter if the script is missing) and present:

- Per milestone M0..M5 (docs/PRD.md section 13): deliverables done / total, exit criteria met? (with the evidence).
- Goals G1..G6 (section 2): current measure vs target (categories covered, workflows shipped, patterns with >= 2
  references, CI status, screenshot coverage).
- The next 5 items to build, in dependency order (patterns before the workflows that use them).

Be blunt about what is planned-but-empty; the PRD warns that an empty repo with a big matrix reads as abandoned.
