"""Shared helpers for the dev scripts: .env loading, n8n internal REST session, public API, compose exec.

Nothing here prints secrets. The public API key is stored in data/.n8n-api-key (git-ignored).
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
ENV_FILE = REPO / ".env"
API_KEY_FILE = REPO / "data" / ".n8n-api-key"


def read_env(path: Path = ENV_FILE) -> dict[str, str]:
    env: dict[str, str] = {}
    example = REPO / ".env.example"
    for p in (example, path):
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k in env})
    return env


def base_url(env: dict[str, str] | None = None) -> str:
    env = env or read_env()
    return os.environ.get("N8N_BASE_URL") or f"http://{env.get('N8N_HOST', 'localhost')}:{env.get('N8N_PORT', '5678')}"


def wait_for(url: str, timeout: int = 180, expect: int = 200) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:  # noqa: S310
                if r.status == expect:
                    return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(3)
    return False


class Session:
    """Cookie-authenticated session against n8n's internal REST API (what the editor uses)."""

    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, method: str, path: str, body: dict | None = None, headers: dict | None = None,
                raise_for_status: bool = True) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        req.add_header("browser-id", "automation-lab-scripts")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with self.opener.open(req, timeout=60) as r:
                raw = r.read().decode("utf-8", "replace")
                return r.status, (json.loads(raw) if raw.strip() else None)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                payload = json.loads(raw)
            except Exception:  # noqa: BLE001
                payload = {"message": raw[:500]}
            if raise_for_status:
                raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {payload.get('message', payload)}") from None
            return e.code, payload

    def get(self, path: str, **kw: Any) -> tuple[int, Any]:
        return self.request("GET", path, **kw)

    def post(self, path: str, body: dict | None = None, **kw: Any) -> tuple[int, Any]:
        return self.request("POST", path, body, **kw)

    def login(self, email: str, password: str) -> None:
        # n8n 2.x expects emailOrLdapLoginId; older builds expect email. Sending both is harmless.
        self.post("/rest/login", {"emailOrLdapLoginId": email, "email": email, "password": password})


def api_key() -> str | None:
    if API_KEY_FILE.exists():
        return API_KEY_FILE.read_text(encoding="utf-8").strip() or None
    return os.environ.get("N8N_API_KEY")


def api(method: str, path: str, body: dict | None = None, base: str | None = None) -> Any:
    """Call the public API (/api/v1) with the stored API key."""
    key = api_key()
    if not key:
        raise SystemExit("no API key: run `python scripts/bootstrap.py` first (creates data/.n8n-api-key)")
    url = (base or base_url()).rstrip("/") + "/api/v1" + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-N8N-API-KEY", key)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
            raw = r.read().decode("utf-8", "replace")
            return json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"{method} /api/v1{path} -> HTTP {e.code}: {raw}") from None


def compose(*args: str, capture: bool = True, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", *args]
    return subprocess.run(cmd, cwd=REPO, capture_output=capture, text=True, check=check, timeout=timeout,
                          encoding="utf-8", errors="replace")


def n8n_cli(*args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    """Run an n8n CLI command inside the running n8n container."""
    return compose("exec", "-T", "n8n", "n8n", *args, check=False, timeout=timeout)


def psql(sql: str, db: str | None = None) -> str:
    env = read_env()
    res = compose("exec", "-T", "postgres", "psql", "-U", env.get("POSTGRES_USER", "n8n"),
                  "-d", db or env.get("DEMO_DB", "demo"), "-tAc", sql, check=False)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip())
    return res.stdout.strip()


def front_matter(readme: Path) -> dict[str, Any]:
    import re

    text = readme.read_text(encoding="utf-8", errors="replace")
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    fm: dict[str, Any] = {}
    if not m:
        return fm
    for line in m.group(1).splitlines():
        if ":" not in line or line.startswith(" "):
            continue
        k, v = line.split(":", 1)
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            fm[k.strip()] = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
        elif v.lower() in ("true", "false"):
            fm[k.strip()] = v.lower() == "true"
        else:
            fm[k.strip()] = v.strip("'\"")
    return fm


def workflow_folders() -> list[Path]:
    """All folders with a workflow.json: patterns first, then workflows, each sorted by id."""
    folders: list[Path] = []
    for base in ("patterns", "workflows"):
        d = REPO / base
        if d.exists():
            folders.extend(sorted(p for p in d.iterdir() if (p / "workflow.json").exists()))
    return folders


def eprint(*a: Any) -> None:
    print(*a, file=sys.stderr)
