---
description: Regenerate the coverage matrix in the root README from workflow/pattern front-matter and show the diff.
allowed-tools: Bash(python:*), Bash(git diff:*), Read
---

Run `python scripts/build-matrix.py` then `git diff --stat README.md` and show the matrix section.
Report counts per category (shipped / in-progress / planned) and any warnings about pattern "Used by" mismatches.
Never edit the block between the MATRIX markers by hand.
