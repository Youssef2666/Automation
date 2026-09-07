#!/usr/bin/env python
"""send.py <push|issues> [file.json] [--bad-signature] [--delivery ID] - sign a GitHub-shaped payload and POST it to M02."""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

SECRET = b"lab-demo-key"          # LAB_WEBHOOK_KEY in .env.example (dummy shared secret)
URL = "http://localhost:5678/webhook/m02-github"

argv = list(sys.argv[1:])
if "--delivery" in argv:                       # drop the flag's value from the positional list
    i = argv.index("--delivery"); del argv[i:i + 2]
args = [a for a in argv if not a.startswith("--")]
event = args[0] if args else "push"
path = Path(args[1]) if len(args) > 1 else Path(__file__).with_name("push.json" if event == "push" else "issue.json")
body = path.read_bytes()
sig = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
if "--bad-signature" in sys.argv:
    sig = "sha256=" + "0" * 64
delivery = sys.argv[sys.argv.index("--delivery") + 1] if "--delivery" in sys.argv else str(uuid.uuid4())
req = urllib.request.Request(URL, data=body, method="POST", headers={
    "Content-Type": "application/json", "X-GitHub-Event": event, "X-GitHub-Delivery": delivery,
    "X-Hub-Signature-256": sig, "User-Agent": "GitHub-Hookshot/lab"})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        print(r.status, r.read().decode()[:200], "| delivery", delivery)
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode()[:200], "| delivery", delivery)
