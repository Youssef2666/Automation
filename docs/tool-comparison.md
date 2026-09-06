# Tool comparison: where n8n fits, and where it does not

Written for an engineer deciding what to automate with. Facts are as of 2026 and hedged where vendors move fast;
check current pricing pages before committing. The PRD calls for the same workflow (webhook -> transform -> store
-> notify) built four ways with measured node counts and setup times; that benchmark is scheduled for milestone M5
and is not in this document yet.

## Summary table

| | n8n | Make | Zapier | Power Automate | Node-RED | Temporal / plain code |
|---|---|---|---|---|---|---|
| **Self-hosting** | Yes (Docker, one container + Postgres). Cloud optional | No | No | Cloud flows no; desktop flows run on your machines | Yes, anywhere Node runs | Yes (open source server) or Temporal Cloud |
| **License / pricing model** | Fair-code (Sustainable Use License): free to self-host for internal use; cloud and enterprise features paid per execution/seat | SaaS, priced per operation (each module run counts) | SaaS, priced per task, connectors gated by plan | Per-user or per-flow licences; premium connectors and RPA extra; bundled with some M365 plans | Apache 2.0, free | MIT server; you pay for compute and engineering time |
| **Expressions and code** | `{{ }}` expressions (JS), JS/Python Code node, HTTP node for anything else | Built-in functions, limited code | "Code by Zapier" (JS/Python) with limits | Workflow Definition Language expressions; Office Scripts / Azure Functions for code | JavaScript function nodes, full Node runtime | Everything is code in Go, Java, TS, Python, .NET |
| **Error handling** | Per-node retry and continue-on-fail; error workflow per workflow; explicit branches (see P01, P02) | Per-module error handlers (ignore, resume, rollback, break) | Auto-replay on paid plans; limited branching | Retry policies per action; scopes with run-after conditions as try/catch | Catch and Status nodes; you build the rest | Retries, timeouts and compensation are first-class in the SDK |
| **Versioning** | JSON export (this repo), workflow history in UI; Git-backed environments are a paid feature | Scenario versions in the UI; no native git | Version history in the UI; no native git | Solutions and ALM pipelines; export as zip | `flows.json` in git, projects feature | Code in git, like everything else |
| **AI features** | LangChain-based nodes: agents, chains, vector stores, local models via Ollama | AI modules against hosted providers | Zapier Agents and AI steps against hosted providers | Copilot and AI Builder credits | Community nodes; bring your own | Whatever library you call |
| **Offline capability** | Full: this lab runs with no internet after image pull | None | None | Desktop flows partially; cloud flows none | Full | Full |
| **Where it shines** | Self-hosted glue between APIs, databases and local services; visible logic for mixed teams | Fast visual building with many connectors, non-developers | Largest connector catalog, zero ops | Microsoft 365 / Dynamics estates, desktop RPA | IoT, MQTT, edge devices, hardware | Long-running, transactional, high-volume workflows with tests and code review |

## Where n8n is the wrong tool

Being honest here is the point of this page.

- **High-throughput event processing.** n8n executes one workflow run per event with full data snapshots; tens of
  thousands of events per minute want a queue consumer or a stream processor, not a workflow engine.
- **Multi-step transactions with compensation.** A booking that must roll back three external calls if the fourth
  fails is a saga; Temporal (or a careful hand-written state machine) models that with less risk than If nodes.
- **Logic that needs unit tests and code review.** Workflow JSON diffs are hard to review (this repo mitigates it
  with generated JSON and pretty-printing, but does not solve it). If the business rules are the product, write code.
- **Teams with no one to operate a container.** Zapier or Make trade money for zero operations. That is a valid
  trade.
- **Microsoft-only estates with desktop RPA needs.** Power Automate Desktop plus the M365 connectors is hard to beat
  inside that wall.
- **Edge and hardware.** Node-RED on a Raspberry Pi talking MQTT is the established answer; n8n is heavier and
  has no serial or GPIO story.
- **Anything that must not run on a fair-code licence.** The Sustainable Use License allows internal business use
  and forbids offering n8n itself as a hosted service; read it before building a product on it.

## When to pick which

| Situation | Pick |
|---|---|
| Internal integrations, self-hosted, data must stay on-prem, mixed technical team | n8n |
| Marketing / ops team needs connectors today, no engineers on call | Zapier (breadth) or Make (price per operation, visual debugging) |
| Everything is in Microsoft 365 and someone already pays for it | Power Automate |
| Sensors, devices, local network protocols | Node-RED |
| Payments, orders, anything with money and retries across services | Temporal or code with a proper job queue |
| Cron plus a script would do | Cron plus a script; a workflow engine adds nothing |

## Why this lab uses n8n

The lab's constraints are: run offline, cost nothing, be legible to a non-developer in a screenshot, and show
production concerns rather than happy paths. n8n is the only tool in the table that satisfies all four: it
self-hosts in one container, its Community Edition is free for this use, its canvas is the screenshot, and it has
enough surface (error workflows, retries, sub-workflows, Redis and Postgres nodes, local LLM nodes) to demonstrate
the patterns in `patterns/` without hiding the failure modes. The trade-offs that come with it, JSON diffs, one
run per event, and a fair-code licence, are documented above and in the "Notes & trade-offs" section of each
workflow README rather than papered over.

Related: [ADR 0001 (version pin)](decisions/0001-n8n-version-pin.md),
[ADR 0003 (local services instead of SaaS)](decisions/0003-local-services-instead-of-saas.md),
[ADR 0005 (GitHub Actions items in the DevOps category)](decisions/0005-github-actions-workflows-in-devops-category.md).
