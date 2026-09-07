# Security: threat model of a local lab

This stack is built to run on one machine, bound to `localhost`, with dummy credentials and synthetic data. It is
**not** a hardened deployment. This page says what is protected, what is deliberately not, and what to change before
any port leaves your machine.

## What we protect against

| Threat | Control | Where |
|---|---|---|
| A credential lands in the public repo | `.env` is git-ignored; only `.env.example` with dummy values is tracked. A regex secret scan (AWS, OpenAI, GitHub, Slack, Google, Telegram, Stripe, SendGrid keys, private-key blocks, JWTs) blocks Claude Code writes and fails CI | `.gitignore`, `scripts/validate.py`, `.claude/hooks/guard_secrets.py`, `.github/workflows/validate.yml` |
| A credential lands in a workflow export | Workflows reference credentials as `{id, name}` only; `scripts/export-workflows.sh` strips credential data, `pinData` and instance metadata before writing back | `scripts/dev/strip-export.py`, validator rule "credentials by {id,name}" |
| Real personal data ends up in seed or test files | Only `@lab.local` / `@example.com` addresses pass the validator in content directories; the seed generator uses word lists, not real names | `scripts/validate.py` (`ALLOWED_EMAIL_DOMAINS`), `seed/generate_seed.py` |
| A workflow reads container secrets | `N8N_BLOCK_ENV_ACCESS_IN_NODE=true`; the validator rejects `$env` in any node parameter. Configuration reaches workflows through credentials created at bootstrap, or through Set nodes | `docker-compose.yml`, [ADR 0006](decisions/0006-env-access-blocked-in-nodes.md) |
| A workflow runs shell commands in the n8n container | `NODES_EXCLUDE=["n8n-nodes-base.executeCommand"]`; Python Code nodes are also outside the allowlist | `docker-compose.yml`, validator node allowlist |
| A workflow reads or writes arbitrary container paths | `N8N_RESTRICT_FILE_ACCESS_TO=/home/node/.n8n-files` and `N8N_BLOCK_FILE_ACCESS_TO_N8N_FILES=true`. Mounts: `seed/files` (ro), `data/` (rw), `docs/` and `patterns/` (ro) | `docker-compose.yml` `x-n8n-volumes` |
| Anyone on the network fires a webhook | Webhook nodes use header auth with the `Webhook - header auth` credential (`X-Lab-Key: <LAB_WEBHOOK_KEY>`); outbound calls attach the same credential through the P07 sub-workflow (host allowlist, key never on the canvas); payload-signed webhooks (GitHub HMAC) are verified in M02 with the Crypto node and carry the dummy secret as P07's documented exception | `scripts/bootstrap.py` credential `ALcredWebhookHdr`, `patterns/P07-secrets/` (decision table: where every kind of value lives) |
| Telemetry and update pings leave the machine | `N8N_DIAGNOSTICS_ENABLED=false`, `N8N_VERSION_NOTIFICATIONS_ENABLED=false`, `N8N_TEMPLATES_ENABLED=false` | `docker-compose.yml` |

## How credentials get into n8n

1. You copy `.env.example` to `.env` and (optionally) change the dummy values.
2. `bash scripts/setup.sh` runs `scripts/bootstrap.py`, which creates the owner account (`N8N_OWNER_EMAIL`,
   `N8N_OWNER_PASSWORD`), mints a public API key into `data/.n8n-api-key` (git-ignored), and imports the canonical
   credentials with `n8n import:credentials` from a temp file that is deleted afterwards.
3. n8n encrypts credentials at rest with `N8N_ENCRYPTION_KEY`. Leave it empty and n8n generates one on first boot
   and stores it in the `n8n_data` volume; set it explicitly if you ever want to move the volume.

No script prints `.env` contents or decrypted credentials, and CI logs never contain them (`.env` in CI is a copy
of `.env.example`).

## What is intentionally weak on localhost

- Postgres, Redis, MinIO, Mailpit, GreenMail and mock-api publish their ports on the host with default or empty
  passwords (`minioadmin`, `n8n`, GreenMail auth disabled). That is convenient for `psql` and the MinIO console and
  acceptable only because nothing listens beyond `127.0.0.1` on a typical desktop.
- `N8N_SECURE_COOKIE=false` because the editor is served over plain `http://localhost`.
- The webhook header key in `.env.example` is a well-known dummy value.
- n8n Community Edition has no SSO, no RBAC beyond owner/member, and no audit log.

## Before exposing anything beyond localhost

Do all of these, in this order, or do not expose it.

1. Put n8n behind a reverse proxy with TLS (Caddy or Traefik), set `N8N_PROTOCOL=https`, `N8N_SECURE_COOKIE=true`,
   `N8N_HOST` and `WEBHOOK_URL` to the public hostname.
2. Rotate every value in `.env`: owner password, `LAB_WEBHOOK_KEY`, `POSTGRES_PASSWORD`, `MINIO_ROOT_*`, and set a
   fixed `N8N_ENCRYPTION_KEY` that you back up.
3. Remove the host `ports:` entries for Postgres, Redis, MinIO API, GreenMail and mock-api; only the proxy needs to
   be reachable. Keep Mailpit and Metabase behind the proxy with auth or drop them.
4. Enable auth on GreenMail or replace it with a real IMAP account; Mailpit's `MP_SMTP_AUTH_ACCEPT_ANY` must go.
5. Regenerate the n8n API key (`data/.n8n-api-key`) and restrict its scopes to what O04/O05 need.
6. Turn on `EXECUTIONS_DATA_SAVE_ON_SUCCESS=none` or shorten `EXECUTIONS_DATA_MAX_AGE` if executions carry
   personal data; execution data is stored unencrypted in Postgres.
7. Consider n8n's own hardening guide for anything multi-user (SSO, RBAC and log streaming are paid features).

## Secret scanning in practice

```bash
python scripts/validate.py --secrets-only                          # working tree
git log --all -p | python scripts/validate.py --secrets-stdin       # history
git ls-files | grep -E '(^|/)\.env(\.|$)' | grep -v example         # any tracked env file?
```

If a real secret was ever committed: rotate it first, then rewrite history. The scripts do not rewrite history for
you, and the Claude Code Bash guard refuses force pushes.

## Reporting

See [SECURITY.md](../SECURITY.md) at the repo root.
