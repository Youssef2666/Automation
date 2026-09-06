# 0001 - Pin n8n to 2.37.10 and allowlist node typeVersions

Date: 2026-09-06
Status: accepted

**Context**: PRD section 14 names version drift as a risk: exported JSON rots silently when node parameter shapes
change, and the README template's "Tested on: n8n 1.x" is too loose to reproduce.
**Decision**: `docker-compose.yml` pins `n8nio/n8n:2.37.10` for `n8n` and `n8n-worker`. Workflows may use only the
node types and `typeVersion`s listed in `.claude/skills/n8n-workflow-json/SKILL.md`; `scripts/validate.py` enforces
the same table (unknown type = error, other version = warning). Front-matter carries `tested_on: n8n 2.37.10`.
Dependabot never touches this tag; bumps are manual and re-run the compose smoke test.
**Consequences**: Every export imports into the engine the author used. A bump means re-checking the allowlist
against the release notes, re-importing everything and updating `tested_on` in one commit. Old typeVersions keep
working because n8n never removes them, so the pin can lag without breaking anything.
