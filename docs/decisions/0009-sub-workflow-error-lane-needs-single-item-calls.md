# 0009 - An Execute Workflow error lane is only trusted on single-item calls

Date: 2026-09-07
Status: accepted

**Context**: PRD 9.8 (P05) assumes a caller can wire an Execute Workflow node's error output and recover when a shared
block refuses its input. Eleven throwaway probes plus P05's harness, all against the shipped `P05 - Lookup customer`
(`ALP05SubWorkflow`, Execute Workflow v1.1, n8n 2.37.10), say that holds for **one** item only: single-item calls were
right in every shape tried - refusal as one item on output index 1 in `each` and in `once` mode, a good item as one
result on index 0, and with no lane wired the run fails carrying the block's Stop and Error message. Batches with a
failing item were not, and failed in four different ways depending on the item count and on where the failure sat.
1 good + 1 bad reported status **success** with the failing item silently gone and no error recorded anywhere; 2 bad
returned only the first error; 2 good + 1 bad (failure last) grew a **third** output branch and delivered the error on
index 2; 1 bad + 2 good (failure first) put it back on index 1; 3 good + 1 bad crashed the node (`TypeError: Cannot
read properties of undefined (reading 'entries')`, `assignPairedItems`, `workflow-execute.ts:2865`, in executions 771
and 824) and echoed its four raw input items on output 0. Batches with nothing failing were always fine, up to 4 items.
So the branch index is a moving target, not a constant: the earlier `error_output_index: 2` recorded in P05's fixtures
and the "index 2" comment in `patterns/P07-secrets/test/run.py` were both honest observations of their own shape. The
block itself behaved correctly throughout (4 executions, 3 success + 1 error in the harness run), so the defect is the
caller's node - and the silent drop, not the crash, is the dangerous case: a crash at least announces itself.
**Decision**: A caller that must handle a sub-workflow failure on the error lane calls the block one item at a time; a
batch call either accepts that one failure fails the whole run, or carries no error lane. A shared block that can
legitimately fail is designed so its normal negative outcome is data rather than an exception - P05 answers a miss
with `ok: true, found: false` - which keeps failures off the lane at all; this engine bug is a second, independent
argument for that contract.
**Consequences**: No shipped workflow is at risk: the pairing exists only in test fixtures - P05's harness, which
calls the failing case on its own, and the throwaway caller `patterns/P07-secrets/test/run.py` builds at run time -
and the other `continueErrorOutput` nodes (M01, P02, P06, P07) are HTTP Request, a different node type. P07's test
passes today (verified: 2 results + 1 error item, 3 passed 0 failed) because it reads *every* branch after 0 rather
than a fixed index, but it is a 3-item batch with the refusal last: one more good case ahead of it and it would be
the crash shape. Left as is, flagged here. Cost: per-item calls wherever a caller wants recovery. This is an upstream
bug we route around, pinned to the version in ADR 0001 - re-run the probe matrix on the next n8n bump before relaxing
the rule.
