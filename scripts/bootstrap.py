#!/usr/bin/env python
"""bootstrap.py - make a fresh n8n instance usable without clicking anything.

Idempotent steps (run from the repo root, stack already up):
  1. wait for n8n              GET  /healthz
  2. owner account             POST /rest/owner/setup   (skipped when already set up)
  3. login                     POST /rest/login
  4. public API key            POST /rest/api-keys      -> data/.n8n-api-key (git-ignored)
  5. credentials               n8n import:credentials   (canonical ids, values from .env)
  6. workflows                 n8n import:workflow --separate (patterns first)
  7. publish                   POST /api/v1/workflows/{id}/activate for README front-matter autopublish: true
  8. marker                    data/.bootstrapped

Flags: --skip-workflows  --no-publish  --publish-all  --only-credentials  --base-url URL
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "dev"))
from _n8n import (API_KEY_FILE, REPO, Session, api, base_url, compose, eprint, front_matter, n8n_cli,  # noqa: E402
                  read_env, wait_for, workflow_folders)

# Canonical credentials - keep in sync with CREDS in .claude/skills/n8n-workflow-json/n8n_builder.py
def credential_specs(env: dict[str, str]) -> list[dict]:
    specs = [
        {"id": "ALcredPostgresDm", "name": "Postgres - demo", "type": "postgres",
         "data": {"host": "postgres", "port": 5432, "database": env.get("DEMO_DB", "demo"),
                  "user": env.get("POSTGRES_USER", "n8n"), "password": env.get("POSTGRES_PASSWORD", "n8n"),
                  "ssl": "disable", "allowUnauthorizedCerts": False, "sshTunnel": False}},
        {"id": "ALcredRedisLocal", "name": "Redis - local", "type": "redis",
         "data": {"host": env.get("REDIS_HOST", "redis"), "port": int(env.get("REDIS_PORT", "6379")),
                  "database": 0, "password": "", "ssl": False}},
        {"id": "ALcredSmtpMailpt", "name": "SMTP - Mailpit", "type": "smtp",
         "data": {"user": "", "password": "", "host": env.get("SMTP_HOST", "mailpit"),
                  "port": int(env.get("SMTP_PORT", "1025")), "secure": False, "disableStartTls": True,
                  "hostname": "automation-lab", "allowUnauthorizedCerts": True}},
        {"id": "ALcredImapGreenM", "name": "IMAP - GreenMail", "type": "imap",
         "data": {"user": env.get("IMAP_USER", "inbox"), "password": env.get("IMAP_PASSWORD", "inbox"),
                  "host": env.get("IMAP_HOST", "greenmail"), "port": int(env.get("IMAP_PORT", "3143")),
                  "secure": False, "allowUnauthorizedCerts": True}},
        {"id": "ALcredS3MinioLoc", "name": "S3 - MinIO", "type": "s3",
         "data": {"accessKeyId": env.get("MINIO_ROOT_USER", "minioadmin"),
                  "secretAccessKey": env.get("MINIO_ROOT_PASSWORD", "minioadmin"), "region": "us-east-1",
                  "endpoint": "http://minio:9000", "forcePathStyle": True, "ignoreSSLIssues": False}},
        {"id": "ALcredOllamaLocl", "name": "Ollama - local", "type": "ollamaApi",
         "data": {"baseUrl": "http://ollama:11434"}},
        {"id": "ALcredQdrantLocl", "name": "Qdrant - local", "type": "qdrantApi",
         "data": {"qdrantUrl": env.get("QDRANT_URL", "http://qdrant:6333"), "apiKey": ""}},
        {"id": "ALcredWebhookHdr", "name": "Webhook - header auth", "type": "httpHeaderAuth",
         "data": {"name": "X-Lab-Key", "value": env.get("LAB_WEBHOOK_KEY", "lab-demo-key")}},
    ]
    if env.get("TELEGRAM_BOT_TOKEN"):
        specs.append({"id": "ALcredTelegramBt", "name": "Telegram - bot", "type": "telegramApi",
                      "data": {"accessToken": env["TELEGRAM_BOT_TOKEN"], "baseUrl": "https://api.telegram.org"}})
    if env.get("GITHUB_TOKEN"):
        specs.append({"id": "ALcredGithubTokn", "name": "GitHub - token", "type": "githubApi",
                      "data": {"server": "https://api.github.com", "user": "", "accessToken": env["GITHUB_TOKEN"]}})
    return specs


def step(msg: str) -> None:
    print(f"\n==> {msg}", flush=True)


def ensure_owner(sess: Session, env: dict[str, str]) -> None:
    status, settings = sess.get("/rest/settings")
    um = (settings or {}).get("data", {}).get("userManagement", {})
    needs_setup = um.get("showSetupOnFirstLoad", False)
    email = env.get("N8N_OWNER_EMAIL", "owner@lab.local")
    if needs_setup:
        step(f"creating owner account {email}")
        sess.post("/rest/owner/setup", {
            "email": email,
            "firstName": env.get("N8N_OWNER_FIRST_NAME", "Lab"),
            "lastName": env.get("N8N_OWNER_LAST_NAME", "Owner"),
            "password": env.get("N8N_OWNER_PASSWORD", "LabOwner2026x"),
        })
        print("   owner created")
    else:
        step("owner account already exists - logging in")
        sess.login(email, env.get("N8N_OWNER_PASSWORD", "LabOwner2026x"))
    status, me = sess.get("/rest/login")
    who = (me or {}).get("data", {}) or {}
    print(f"   logged in as {who.get('email', email)} (role {who.get('role', '?')})")


def ensure_api_key(sess: Session) -> str:
    if API_KEY_FILE.exists() and API_KEY_FILE.read_text(encoding="utf-8").strip():
        # Validate the stored key still works.
        try:
            api("GET", "/workflows?limit=1")
            step("public API key present and valid")
            return API_KEY_FILE.read_text(encoding="utf-8").strip()
        except Exception:  # noqa: BLE001
            eprint("   stored API key rejected - creating a new one")
    step("creating public API key")
    scopes: list[str] = []
    status, payload = sess.get("/rest/api-keys/scopes", raise_for_status=False)
    if status == 200 and isinstance(payload, dict):
        data = payload.get("data", payload)
        if isinstance(data, list):
            scopes = [s if isinstance(s, str) else s.get("scope") or s.get("name") for s in data]
    body: dict = {"label": f"automation-lab {time.strftime('%Y-%m-%d %H:%M')}", "expiresAt": None}
    if scopes:
        body["scopes"] = [s for s in scopes if s]
    status, payload = sess.post("/rest/api-keys", body, raise_for_status=False)
    if status >= 400 and "scopes" in json.dumps(payload):
        status, payload = sess.post("/rest/api-keys", {"label": body["label"], "expiresAt": None}, raise_for_status=False)
    if status >= 400:
        raise RuntimeError(f"could not create API key: HTTP {status} {payload}")
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    raw = data.get("rawApiKey") or data.get("apiKey")
    if not raw:
        raise RuntimeError(f"API key response had no rawApiKey: {list(data)}")
    API_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    API_KEY_FILE.write_text(raw + "\n", encoding="utf-8")
    print(f"   saved to {API_KEY_FILE.relative_to(REPO)}")
    return raw


def import_credentials(env: dict[str, str], api_key_value: str, base: str) -> None:
    step("importing canonical credentials (n8n import:credentials)")
    specs = credential_specs(env)
    # n8n API credential - lets workflows (O04, O05) talk to this instance.
    specs.append({"id": "ALcredN8nApiLocl", "name": "n8n API - local", "type": "n8nApi",
                  "data": {"apiKey": api_key_value, "baseUrl": "http://localhost:5678/api/v1"}})
    tmp = Path(tempfile.mkdtemp()) / "credentials.json"
    tmp.write_text(json.dumps(specs), encoding="utf-8")
    try:
        compose("cp", str(tmp), "n8n:/tmp/automation-lab-credentials.json")
        res = n8n_cli("import:credentials", "--input=/tmp/automation-lab-credentials.json")
        tail = (res.stdout + res.stderr).strip().splitlines()[-3:]
        print("   " + " | ".join(tail) if tail else "   done")
        if res.returncode != 0:
            raise RuntimeError("import:credentials failed:\n" + res.stdout + res.stderr)
    finally:
        compose("exec", "-T", "n8n", "rm", "-f", "/tmp/automation-lab-credentials.json", check=False)
        shutil.rmtree(tmp.parent, ignore_errors=True)
    print(f"   {len(specs)} credentials imported: " + ", ".join(s["name"] for s in specs))


def import_workflows(folders: list[Path]) -> list[Path]:
    if not folders:
        step("no workflow folders found - nothing to import")
        return []
    step(f"importing {len(folders)} workflows (n8n import:workflow --separate)")
    staging = "/tmp/automation-lab-import"
    files = " ".join(f'"/repo/{f.relative_to(REPO).as_posix()}/workflow.json"' for f in folders)
    script = (f"rm -rf {staging} && mkdir -p {staging} && for f in {files}; do "
              f"cp \"$f\" {staging}/$(basename $(dirname \"$f\")).json; done && ls {staging} | wc -l")
    res = compose("exec", "-T", "n8n", "sh", "-c", script, check=False)
    if res.returncode != 0:
        raise RuntimeError("staging failed: " + res.stderr)
    res = n8n_cli("import:workflow", "--separate", f"--input={staging}", timeout=900)
    out = (res.stdout + res.stderr)
    ok = res.returncode == 0
    summary = [l for l in out.splitlines() if "import" in l.lower() or "error" in l.lower()][-5:]
    print("   " + ("\n   ".join(summary) if summary else out.strip()[-300:]))
    if not ok:
        raise RuntimeError("import:workflow failed:\n" + out)
    return folders


def publish(folders: list[Path], publish_all: bool) -> None:
    targets = []
    for f in folders:
        fm = front_matter(f / "README.md") if (f / "README.md").exists() else {}
        wf = json.loads((f / "workflow.json").read_text(encoding="utf-8"))
        if publish_all or fm.get("autopublish"):
            targets.append((wf.get("id"), wf.get("name"), f))
    if not targets:
        step("nothing marked autopublish - workflows stay unpublished (open one and click Publish)")
        return
    step(f"publishing {len(targets)} workflows")
    for wid, name, folder in targets:
        try:
            api("POST", f"/workflows/{wid}/activate")
            print(f"   published {name}")
        except Exception as exc:  # noqa: BLE001
            eprint(f"   public API activate failed for {name}: {exc}")
            res = n8n_cli("publish:workflow", f"--id={wid}")
            print("   fallback CLI publish " + ("ok" if res.returncode == 0 else "FAILED: " + (res.stdout + res.stderr)[-200:]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--skip-workflows", action="store_true")
    ap.add_argument("--no-publish", action="store_true")
    ap.add_argument("--publish-all", action="store_true")
    ap.add_argument("--only-credentials", action="store_true")
    ap.add_argument("--timeout", type=int, default=240)
    args = ap.parse_args()

    env = read_env()
    base = args.base_url or base_url(env)
    if not (REPO / ".env").exists():
        eprint("warning: .env not found - using .env.example defaults (copy it: cp .env.example .env)")
    step(f"waiting for n8n at {base}/healthz")
    if not wait_for(base + "/healthz", timeout=args.timeout):
        eprint("n8n did not become healthy; check `docker compose logs n8n`")
        return 1
    print("   healthy")

    sess = Session(base)
    ensure_owner(sess, env)
    key = ensure_api_key(sess)
    import_credentials(env, key, base)
    if args.only_credentials:
        return 0
    folders = [] if args.skip_workflows else import_workflows(workflow_folders())
    if folders and not args.no_publish:
        publish(folders, args.publish_all)
    marker = REPO / "data" / ".bootstrapped"
    marker.parent.mkdir(exist_ok=True)
    marker.write_text(time.strftime("%Y-%m-%dT%H:%M:%S\n"), encoding="utf-8")
    step("done")
    print(f"   open {base}  (login: {env.get('N8N_OWNER_EMAIL', 'owner@lab.local')} / password from .env)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as exc:
        eprint(f"\nbootstrap failed: {exc}")
        sys.exit(1)
