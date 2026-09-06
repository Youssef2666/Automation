# 0004 - Workflow JSON is generated, not hand-written

Date: 2026-09-06
Status: accepted

**Context**: The PRD assumes workflows are built in the editor and exported. Editor exports carry random node ids,
instance metadata and unstable key order: diffs are unreadable (PRD risk table) and cross-references (error
workflow, sub-workflow ids) break across imports.
**Decision**: Each workflow has an authoring script in `.claude/skills/n8n-workflow-json/authoring/<ID>_<slug>.py`
using `n8n_builder.py`: deterministic 16-char ids (`wf_id(code, slug)`), credentials as `{id, name}` from `CREDS`,
canonical key order, `pinData: {}`, `active: false`, auto layout. Editor round-trips go through
`scripts/export-workflows.sh` (strips credentials and metadata); ids and connections are never hand-edited.
**Consequences**: Diffs show intent; P01's id can be referenced before P01 is imported; screenshots can be rendered
from the JSON. Cost: a small DSL to maintain, and a node without a helper needs `wf.add(...)` plus a new helper.
The editor is where you debug, not where you author.
