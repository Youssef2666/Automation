---
id: __ID__
title: __TITLE__
category: Patterns
difficulty: Intermediate
status: in-progress
patterns: []
services: [core]
tested_on: n8n 2.37.10
---

# __ID__ - __TITLE__

**Category:** Patterns · **Difficulty:** Intermediate · **Tested on:** n8n 2.37.10

## Problem

What breaks in real deployments when this concern is ignored. Two or three sentences, concrete.

## Pattern

The rule, stated once. Then the decision points (when to apply, when not to).

## Implementation in n8n

1. Node-by-node description of `workflow.json` (if this pattern ships a reusable sub-workflow).
2. How another workflow uses it (Execute Workflow node, settings, expressions).

![screenshot](assets/screenshot.png)

## Trade-offs

- Cost / complexity added
- Failure modes the pattern does not cover

## Used by

- `T01 - Webhook to Database`
- `D04 - Incremental Sync`
