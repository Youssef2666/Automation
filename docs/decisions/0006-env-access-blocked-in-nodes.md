# 0006 - Environment access blocked in nodes; configuration via credentials and Set nodes

Date: 2026-09-06
Status: accepted

**Context**: PRD Appendix B lists environment variables and the obvious shortcut is `{{ $env.X }}` in workflows.
That couples exports to one machine's `.env` and, in a public repo, invites reading `N8N_ENCRYPTION_KEY` or database
passwords from a Code node. n8n 2.x ships `N8N_BLOCK_ENV_ACCESS_IN_NODE` for exactly this.
**Decision**: `N8N_BLOCK_ENV_ACCESS_IN_NODE=true` in `docker-compose.yml`; `scripts/validate.py` rejects `$env`
anywhere in a workflow. Values reach workflows only through the canonical credentials `scripts/bootstrap.py` creates
from `.env` (hosts, passwords, webhook header key) or through an Edit Fields (Set) node at the top of the workflow
for non-secret knobs. Execute Command is excluded (`NODES_EXCLUDE`); Python Code nodes are outside the allowlist.
**Consequences**: A workflow imports unchanged on any bootstrapped instance, and every knob is visible on the
canvas. Cost: some duplicated constants across workflows, and the model name lives in a Set node rather than
`OLLAMA_MODEL` (the pull script still reads the variable).
