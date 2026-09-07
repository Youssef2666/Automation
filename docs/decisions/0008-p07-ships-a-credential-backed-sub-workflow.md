# 0008 - P07 ships a credential-backed sub-workflow next to the docs; mock-api gains a key-checked endpoint

Date: 2026-09-07
Status: accepted

**Context**: PRD 9.8 lists P07 as documentation (`.env` conventions, credential naming, what never gets committed)
and `scripts/validate.py` treats it as doc-only. Every other pattern is proven by a live execution; a rule about
secrets that cannot be exercised stays an opinion, and the lab had no outbound example of "the key comes from the
credential, not from the canvas or `$env`".
**Decision**: P07 keeps the docs (decision table, `pre-commit` guard, CI scan) and additionally ships
`P07 - Signed request (credential-backed)`: an HTTP Request sub-workflow that carries the `Webhook - header auth`
credential and a host allowlist in a Set node. mock-api gets `/secure/ping`, which answers 200 only with the right
`X-Lab-Key` (from `LAB_WEBHOOK_KEY`, dummy default, passed through compose) and never echoes it, so the check runs
offline. P07 leaves the validator's `DOC_ONLY_IDS` (now empty; doc-only folders declare `doc_only: true`), so its
`workflow.json`, screenshot and tests are required like everywhere else.
**Consequences**: P07 has execution evidence like every other pattern and one Execute Workflow node adopts it.
Cost: one more mock-api endpoint and a compose environment line. The HMAC case (M02) stays a documented exception
because no credential type feeds the Crypto node.
