---
paths:
  - "patterns/**"
---

# Rules for patterns/

- Same folder contract as workflows, category `Patterns`, ids `P01..P08` (new patterns continue the sequence).
- A pattern that ships a sub-workflow follows the P05 sub-workflow contract: passthrough trigger, single flat
  output item with `ok` and `response`, clear `Stop and Error` on bad input.
- The README "Used by" list must agree with the `patterns:` front-matter of the workflows that reference it.
- Patterns are explained with the failure story first, then the rule, then the n8n implementation, then trade-offs.
- P07 (secrets) is documentation only and is the reference for `.env.example`, the hooks and the CI secret scan.
