# 0005 - O01-O03 are GitHub Actions, documented as doc-only catalog items

Date: 2026-09-06
Status: accepted

**Context**: The PRD DevOps category lists O01 (validate on PR), O02 (changelog and release) and O03 (issue triage).
GitHub Actions does those natively and for free; as n8n workflows they would need a GitHub token on the core path,
which goal G3 forbids, and n8n would be the wrong tool.
**Decision**: O01, O02 and O03 are `.github/workflows/validate.yml`, `release.yml` and `triage.yml`. Their catalog
folders keep the README contract (front-matter, Problem, How it works, Notes) but ship no `workflow.json`; they are
doc-only items like P07 and appear in the matrix with the other DevOps rows. O04 and O05 remain real n8n workflows.
**Consequences**: The DevOps row is honest about what runs where, and CI becomes a documented artefact. The
validator's doc-only set (`DOC_ONLY_IDS`, today only P07) must gain O01-O03 when those folders are scaffolded, and
"36 workflows" in the README counts three that are not n8n workflows.
