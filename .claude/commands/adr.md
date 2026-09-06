---
description: Write an Architecture Decision Record in docs/decisions/ for a deviation from the PRD or a notable technical choice.
argument-hint: "<title>" - <one-line context>
allowed-tools: Bash(ls:*), Read, Write, Glob
---

Create `docs/decisions/NNNN-<kebab-title>.md` (next free number, four digits) for: $ARGUMENTS

Format (15 lines max): `# NNNN - Title`, `Date: YYYY-MM-DD`, `Status: accepted`, then **Context**, **Decision**,
**Consequences** (including what the PRD said if this deviates from it). Add a bullet linking it from
`docs/decisions/README.md`. Be specific about versions, env vars and service names.
