# Contributing

Thanks for looking. This repo is a curated coverage matrix, not a dump of workflows, so contributions are held to
the same contract as the author's own folders. Everything below runs from the repo root on Linux, macOS or Git Bash.

## Ground rules

1. **No secrets, no real data.** Never commit `.env`; only `.env.example` with dummy values. Emails end in
   `@lab.local` or `@example.com`. No real people, clients or employers, not even in test payloads. The secret scan
   in `scripts/validate.py` is blocking in CI.
2. **Runs locally and free.** The core path of every item needs no cloud account, API key or card. Optional
   external variants (Telegram, GitHub token) are documented as optional and never required.
3. **n8n 2.37.10 is pinned.** Use only node types and versions from the allowlist in
   `.claude/skills/n8n-workflow-json/SKILL.md`. No Execute Command, no Python Code nodes, no `$env` in workflows
   (see [ADR 0006](docs/decisions/0006-env-access-blocked-in-nodes.md)).
4. **Generate, do not hand-write JSON.** `workflow.json` comes from an authoring script using `n8n_builder.py`, or
   from `bash scripts/export-workflows.sh`. Never hand-edit ids or connections.
5. **Synthetic data only.** Extend `seed/generate_seed.py` and regenerate; do not edit `seed/seed.sql` by hand.

## The folder contract

Every `workflows/<ID>-<slug>/` and `patterns/<PNN>-<slug>/` folder contains:

| File | Requirement |
|---|---|
| `README.md` | YAML front-matter (`id`, `title`, `category`, `difficulty`, `status`, `patterns`, `services`, `tested_on`) and the sections Problem, How it works, Setup, Try it, Notes & trade-offs (patterns: Problem, Pattern, Implementation, Trade-offs, Used by) |
| `workflow.json` | n8n export, pretty-printed, `pinData: {}`, `active: false`, credentials referenced as `{id, name}` only |
| `assets/screenshot.png` | canvas view, at least 1200 px wide (auto-rendered preview is fine until a real capture exists) |
| `test/` | at least one sample input: payload JSON, CSV row, SQL, `.eml`, audio |

Naming: `<CATEGORY><NN>-<kebab-case-slug>`, for example `A02-ticket-classifier`. The full contract and the
acceptance checklist for `status: shipped` are in `.claude/skills/workflow-folder-contract/SKILL.md`.

## Proposing a new workflow

Open an issue with the "Workflow request" template first if the item is not already in the catalog
(`docs/PRD.md` section 9). Then:

```bash
python scripts/scaffold.py T08 my-slug "My Title" Triggers Intermediate   # folder, README, test/, authoring stub
python .claude/skills/n8n-workflow-json/authoring/T08_my_slug.py           # generate workflow.json
python scripts/render-preview.py workflows/T08-my-slug                     # screenshot preview
python scripts/validate.py workflows/T08-my-slug                           # contract + secret scan
```

With the stack running (`docker compose --profile core up -d && bash scripts/setup.sh`):

```bash
bash scripts/import-workflows.sh workflows/T08-my-slug --publish
# run the README "Try it" commands, then confirm the execution succeeded:
python scripts/dev/executions.py --workflow T08 --last 1
python scripts/build-matrix.py
```

Set `status: shipped` only after the live import and execution pass. Reference at least one pattern and add the
workflow to that pattern's "Used by" list.

## Validation

```bash
python scripts/validate.py                  # every folder; warnings are fine for in-progress items
python scripts/validate.py --strict --docs  # what CI runs: warnings become errors, links are checked
python scripts/validate.py --secrets-only   # repo-wide secret scan
python scripts/build-matrix.py --check      # matrix in README matches front-matter
python -m unittest discover -s scripts/tests
docker compose config -q
```

## Commits and pull requests

Conventional commits, one concern per commit: `feat(T03): cursor polling against mock-api`,
`fix(P02): treat 429 as retryable`, `docs: observability guide`, `ci: pin lychee`. Keep `workflow.json` diffs
readable: regenerate from the authoring script rather than editing in n8n and exporting a reshuffled file.

The PR template carries the checklist. In short:

- [ ] `python scripts/validate.py --strict --docs` passes
- [ ] Screenshot present and at least 1200 px wide
- [ ] README front-matter complete and status honest
- [ ] No secrets, no real emails, no `.env`
- [ ] Imported and executed on n8n 2.37.10; execution id or screenshot in the PR body
- [ ] Any deviation from `docs/PRD.md` has an ADR in `docs/decisions/`

## Code of conduct

See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Security issues: [SECURITY.md](SECURITY.md).
