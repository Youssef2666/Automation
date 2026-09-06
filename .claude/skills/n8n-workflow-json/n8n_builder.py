"""n8n_builder - a small, dependency-free DSL that emits import-ready n8n workflow JSON.

Why this exists
---------------
Hand-writing n8n JSON is error-prone (node ids, connection maps, typeVersions, resource-locator
shapes). Authoring scripts under .claude/skills/n8n-workflow-json/authoring/ describe a workflow
with this DSL and emit `workflow.json` into the right folder. The JSON is the artifact that ships;
the authoring script is the reproducible source of the *initial* version. After a workflow has been
edited inside n8n, `scripts/export-workflows.sh` becomes the source of truth for that folder.

Conventions baked in
--------------------
* Deterministic IDs: workflow id = wf_id(code, slug) (16 chars), node ids = uuid5(workflow id + node name).
* Credentials are referenced by the canonical {id, name} pairs in CREDS (never values).
* Positions are auto-laid-out (left-to-right by topological depth) unless given explicitly.
* Output keys/order mimic a real n8n export so diffs after a round-trip stay small.

Usage
-----
    from n8n_builder import *
    wf = Workflow("T01", "webhook-to-database", "Webhook to Database", tags=["Triggers"])
    hook = webhook(wf, "Webhook", path="t01", response="responseNode")
    ok = respond(wf, "Respond 200", body='={{ { "ok": true } }}')
    wf.chain(hook, ok)
    wf.save()

Run any authoring script with:  python .claude/skills/n8n-workflow-json/authoring/<file>.py
"""
from __future__ import annotations

import json
import re
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

__all__ = [
    "CREDS", "Workflow", "Node", "wf_id", "cred_ref", "uid", "rl",
    # conditions
    "cond", "cond_str", "cond_num", "cond_bool", "cond_exists", "cond_not_empty", "cond_empty", "cond_array_len",
    "cond_regex", "cond_contains", "cond_date_after", "cond_date_before",
    # nodes
    "manual_trigger", "webhook", "respond", "schedule", "form_trigger", "imap_trigger", "file_trigger",
    "telegram_trigger", "telegram_send", "chat_trigger", "sub_trigger", "error_trigger",
    "postgres_query", "postgres_insert", "postgres_upsert", "postgres_select",
    "set_fields", "code", "if_", "switch", "filter_", "http", "email", "redis", "s3_upload", "s3_list",
    "s3_delete", "read_file", "write_file", "extract", "convert", "execute_workflow", "stop_error", "wait",
    "merge", "loop", "aggregate", "split_out", "sort", "limit", "dedupe", "dedupe_prev_runs", "crypto_hash",
    "html_extract", "rss", "compress", "decompress", "markdown", "noop", "n8n_api", "rename_keys", "date_time",
    "xml",
    # langchain
    "ollama_chat", "ollama_embed", "qdrant_store", "doc_loader", "text_splitter", "llm_chain", "output_parser",
    "agent", "memory", "tool_workflow", "tool_code", "info_extractor", "text_classifier", "retriever", "qa_chain",
    "summarize_chain",
]

REPO = Path(__file__).resolve().parents[3]
NS = uuid.UUID("2b5f4f3e-7c1c-4d0e-9b3a-0a0b0c0d0e0f")
TIMEZONE = "Africa/Tripoli"

# Canonical credential references: key -> (n8n credential type, id, display name).
# IDs are exactly 16 chars (nanoid-shaped) and are created by scripts/bootstrap.py from .env.
CREDS: dict[str, tuple[str, str, str]] = {
    "postgres": ("postgres", "ALcredPostgresDm", "Postgres - demo"),
    "redis": ("redis", "ALcredRedisLocal", "Redis - local"),
    "smtp": ("smtp", "ALcredSmtpMailpt", "SMTP - Mailpit"),
    "imap": ("imap", "ALcredImapGreenM", "IMAP - GreenMail"),
    "s3": ("s3", "ALcredS3MinioLoc", "S3 - MinIO"),
    "ollama": ("ollamaApi", "ALcredOllamaLocl", "Ollama - local"),
    "qdrant": ("qdrantApi", "ALcredQdrantLocl", "Qdrant - local"),
    "n8n": ("n8nApi", "ALcredN8nApiLocl", "n8n API - local"),
    "telegram": ("telegramApi", "ALcredTelegramBt", "Telegram - bot"),
    "github": ("githubApi", "ALcredGithubTokn", "GitHub - token"),
    "header": ("httpHeaderAuth", "ALcredWebhookHdr", "Webhook - header auth"),
}


def uid(*parts: str) -> str:
    return str(uuid.uuid5(NS, ":".join(parts)))


def wf_id(code: str, slug: str) -> str:
    """16-char deterministic workflow id, e.g. T01 + webhook-to-database -> ALT01WebhookToDa."""
    camel = "".join(p[:1].upper() + p[1:] for p in re.split(r"[^A-Za-z0-9]+", slug) if p)
    return ("AL" + code + camel)[:16].ljust(16, "0")


# Canonical folder slugs for every catalog item (folder = f"{code}-{slug}", workflow id = catalog_id(code)).
CATALOG: dict[str, str] = {
    "T01": "webhook-to-database", "T02": "daily-digest", "T03": "api-polling", "T04": "imap-attachment-parser",
    "T05": "form-to-record", "T06": "file-watcher", "T07": "telegram-command-router",
    "D01": "csv-import-validation", "D02": "web-scrape-to-json", "D03": "multi-source-aggregation",
    "D04": "incremental-sync", "D05": "db-dump-to-minio",
    "M01": "uptime-monitor", "M02": "github-events-to-chat", "M03": "rss-keyword-digest",
    "M04": "db-threshold-alert", "M05": "exchange-rate-watcher",
    "R01": "pdf-invoice", "R02": "bulk-certificates", "R03": "arabic-rtl-report", "R04": "pdf-to-dataset",
    "A01": "rag-docs-chatbot", "A02": "ticket-classifier", "A03": "audio-to-tasks", "A04": "arabic-ocr",
    "A05": "agent-with-tools", "A06": "article-to-social",
    "B01": "lead-capture-sequence", "B02": "booking-reminders", "B03": "receipt-ocr-rollup", "B04": "support-triage-sla",
    "O01": "actions-validate-lint", "O02": "actions-changelog-release", "O03": "issue-triage-bot",
    "O04": "nightly-workflow-export", "O05": "execution-logs-metabase",
    "P01": "error-handler", "P02": "retry-backoff", "P03": "idempotency", "P04": "rate-limiting",
    "P05": "sub-workflows", "P06": "testing", "P07": "secrets", "P08": "observability",
}


def catalog_id(code: str) -> str:
    """Deterministic workflow id of a catalog item, e.g. catalog_id("P03") for an Execute Workflow node."""
    return wf_id(code, CATALOG[code])


def cred_ref(key: str) -> dict[str, dict[str, str]]:
    ctype, cid, cname = CREDS[key]
    return {ctype: {"id": cid, "name": cname}}


def rl(value: str, mode: str = "list", cached_name: str | None = None) -> dict[str, Any]:
    """Resource locator value ({__rl: true, value, mode})."""
    d: dict[str, Any] = {"__rl": True, "value": value, "mode": mode}
    if cached_name:
        d["cachedResultName"] = cached_name
    return d


class Node:
    def __init__(self, wf: "Workflow", name: str, type_: str, version: float, parameters: dict | None = None,
                 credentials: dict | None = None, position: tuple[int, int] | None = None, **settings: Any):
        self.wf = wf
        self.name = name
        self.type = type_
        self.version = version
        self.parameters = parameters or {}
        self.credentials = credentials or {}
        self.position = position
        # onError, retryOnFail, maxTries, waitBetweenTries, alwaysOutputData, executeOnce, notes, webhookId, disabled
        self.settings = settings
        self.id = uid(wf.id, name)

    # fluent helpers -------------------------------------------------------------------
    def retry(self, tries: int = 3, wait_ms: int = 1000) -> "Node":
        self.settings.update(retryOnFail=True, maxTries=tries, waitBetweenTries=wait_ms)
        return self

    def on_error(self, mode: str = "continueErrorOutput") -> "Node":
        """mode: stopWorkflow | continueRegularOutput | continueErrorOutput"""
        self.settings["onError"] = mode
        return self

    def always_output(self) -> "Node":
        self.settings["alwaysOutputData"] = True
        return self

    def once(self) -> "Node":
        self.settings["executeOnce"] = True
        return self

    def note(self, text: str, in_flow: bool = True) -> "Node":
        self.settings["notes"] = text
        if in_flow:
            self.settings["notesInFlow"] = True
        return self

    def at(self, x: int, y: int) -> "Node":
        self.position = (x, y)
        return self

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "parameters": self.parameters,
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "typeVersion": self.version,
            "position": list(self.position or (0, 0)),
        }
        for k in ("webhookId", "notes", "notesInFlow", "disabled", "retryOnFail", "maxTries", "waitBetweenTries",
                  "onError", "alwaysOutputData", "executeOnce"):
            if k in self.settings and self.settings[k] is not None:
                d[k] = self.settings[k]
        if self.credentials:
            d["credentials"] = self.credentials
        return d


class Workflow:
    def __init__(self, code: str, slug: str, title: str, *, tags: Iterable[str] = (), error_workflow: str | None = None,
                 timezone: str = TIMEZONE, execution_timeout: int | None = None, id: str | None = None,
                 caller_policy: str = "workflowsFromSameOwner", description: str | None = None):
        assert re.fullmatch(r"[TDMRABOP]\d{2}", code), f"bad code {code}"
        assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug), f"bad slug {slug}"
        self.code, self.slug, self.title = code, slug, title
        self.id = id or wf_id(code, slug)
        self.name = f"{code} - {title}"
        self.tags = ["automation-lab", *tags]
        self.nodes: list[Node] = []
        self.conns: dict[str, dict[str, dict[int, list[tuple[str, int]]]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(list)))
        self.error_workflow = error_workflow
        self.timezone = timezone
        self.execution_timeout = execution_timeout
        self.caller_policy = caller_policy
        self.description = description
        self.stickies: list[dict[str, Any]] = []

    # building -----------------------------------------------------------------------
    def add(self, name: str, type_: str, version: float, params: dict | None = None, *, cred: str | None = None,
            pos: tuple[int, int] | None = None, **settings: Any) -> Node:
        if any(n.name == name for n in self.nodes):
            raise ValueError(f"duplicate node name: {name}")
        credentials = cred_ref(cred) if cred else None
        node = Node(self, name, type_, version, params, credentials, pos, **settings)
        self.nodes.append(node)
        return node

    def connect(self, src: Node, dst: Node, out: int = 0, inp: int = 0, kind: str = "main") -> None:
        self.conns[src.name][kind][out].append((dst.name, inp))

    def chain(self, *nodes: Node, out: int = 0) -> None:
        for a, b in zip(nodes, nodes[1:]):
            self.connect(a, b, out=out)
            out = 0

    def attach(self, sub: Node, parent: Node, kind: str, inp: int = 0) -> None:
        """Connect an AI sub-node (model, memory, tool, parser...) to its parent via a non-main lane."""
        self.conns[sub.name][kind][0].append((parent.name, inp))

    def sticky(self, content: str, *, pos: tuple[int, int], width: int = 420, height: int = 180, color: int = 4) -> None:
        idx = len(self.stickies)
        self.stickies.append({
            "parameters": {"content": content, "height": height, "width": width, "color": color},
            "id": uid(self.id, "sticky", str(idx)),
            "name": "Sticky Note" if idx == 0 else f"Sticky Note {idx + 1}",
            "type": "n8n-nodes-base.stickyNote",
            "typeVersion": 1,
            "position": list(pos),
        })

    # layout ---------------------------------------------------------------------------
    def _layout(self) -> None:
        by_name = {n.name: n for n in self.nodes}
        main_in: dict[str, int] = defaultdict(int)
        main_out: dict[str, list[str]] = defaultdict(list)
        sub_of: dict[str, tuple[str, str]] = {}
        for src, kinds in self.conns.items():
            for kind, lanes in kinds.items():
                for _, edges in lanes.items():
                    for dst, _ in edges:
                        if kind == "main":
                            main_in[dst] += 1
                            main_out[src].append(dst)
                        else:
                            sub_of[src] = (dst, kind)
        roots = [n.name for n in self.nodes if main_in[n.name] == 0 and n.name not in sub_of]
        depth: dict[str, int] = {}
        q = deque((r, 0) for r in roots)
        while q:
            name, d = q.popleft()
            if depth.get(name, -1) >= d:
                continue
            depth[name] = d
            for nxt in main_out[name]:
                if depth.get(nxt, -1) < d + 1 and d < 60:
                    q.append((nxt, d + 1))
        cols: dict[int, list[str]] = defaultdict(list)
        for n in self.nodes:
            if n.name in sub_of or n.position:
                continue
            cols[depth.get(n.name, 0)].append(n.name)
        for d, names in cols.items():
            for i, name in enumerate(names):
                by_name[name].position = (d * 260, i * 220)
        # AI sub-nodes: place under their parent, fanned out.
        children: dict[str, list[str]] = defaultdict(list)
        for sub, (parent, _) in sub_of.items():
            children[parent].append(sub)
        for parent, subs in children.items():
            px, py = by_name[parent].position or (0, 0)
            for i, sub in enumerate(subs):
                if by_name[sub].position is None:
                    by_name[sub].position = (px - 40 + i * 200, py + 220)
                for j, sub2 in enumerate(children.get(sub, [])):
                    if by_name[sub2].position is None:
                        by_name[sub2].position = (px - 40 + i * 200 + j * 200, py + 440)

    # output ---------------------------------------------------------------------------
    def build(self) -> dict[str, Any]:
        self._layout()
        connections: dict[str, Any] = {}
        for src, kinds in self.conns.items():
            connections[src] = {}
            for kind, lanes in kinds.items():
                n_lanes = max(lanes) + 1 if lanes else 0
                connections[src][kind] = [
                    [{"node": dst, "type": kind, "index": inp} for dst, inp in lanes.get(i, [])]
                    for i in range(n_lanes)
                ]
        settings: dict[str, Any] = {
            "executionOrder": "v1",
            "timezone": self.timezone,
            "saveManualExecutions": True,
            "saveExecutionProgress": True,
            "callerPolicy": self.caller_policy,
        }
        if self.error_workflow:
            settings["errorWorkflow"] = self.error_workflow
        if self.execution_timeout:
            settings["executionTimeout"] = self.execution_timeout
        doc: dict[str, Any] = {
            "name": self.name,
            "nodes": [n.to_dict() for n in self.nodes] + self.stickies,
            "connections": connections,
            "active": False,
            "settings": settings,
            "pinData": {},
            "versionId": uid(self.id, "version"),
            "meta": {"templateCredsSetupCompleted": True},
            "id": self.id,
            "tags": [{"name": t} for t in self.tags],
        }
        if self.description:
            doc["meta"]["description"] = self.description
        return doc

    def folder(self) -> Path:
        base = "patterns" if self.code.startswith("P") else "workflows"
        return REPO / base / f"{self.code}-{self.slug}"

    def save(self, path: Path | str | None = None) -> Path:
        target = Path(path) if path else self.folder() / "workflow.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.build(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {target.relative_to(REPO)} ({len(self.nodes)} nodes, id={self.id})")
        return target


# =====================================================================================
# Condition helpers (If / Filter / Switch v2 conditions)
# =====================================================================================

def cond(left: str, op_type: str, operation: str, right: Any = None, *, single: bool = False) -> dict[str, Any]:
    c: dict[str, Any] = {"id": uid("cond", left, op_type, operation, json.dumps(right, default=str)),
                         "leftValue": left, "rightValue": "" if right is None else right,
                         "operator": {"type": op_type, "operation": operation}}
    if single:
        c["operator"]["singleValue"] = True
    return c


def cond_str(left: str, operation: str, right: str = "") -> dict:
    """operation: equals | notEquals | contains | notContains | startsWith | endsWith | regex"""
    return cond(left, "string", operation, right)


def cond_num(left: str, operation: str, right: float) -> dict:
    """operation: equals | notEquals | gt | gte | lt | lte"""
    return cond(left, "number", operation, right)


def cond_bool(left: str, true: bool = True) -> dict:
    return cond(left, "boolean", "true" if true else "false", single=True)


def cond_exists(left: str) -> dict:
    return cond(left, "string", "exists", single=True)


def cond_not_empty(left: str) -> dict:
    return cond(left, "string", "notEmpty", single=True)


def cond_empty(left: str) -> dict:
    return cond(left, "string", "empty", single=True)


def cond_array_len(left: str, operation: str, n: int) -> dict:
    """operation: lengthEquals | lengthNotEquals | lengthGt | lengthLt | lengthGte | lengthLte"""
    return cond(left, "array", operation, n)


def cond_regex(left: str, pattern: str) -> dict:
    return cond(left, "string", "regex", pattern)


def cond_contains(left: str, right: str) -> dict:
    return cond(left, "string", "contains", right)


def cond_date_after(left: str, right: str) -> dict:
    return cond(left, "dateTime", "after", right)


def cond_date_before(left: str, right: str) -> dict:
    return cond(left, "dateTime", "before", right)


def _conditions(conds: list[dict], combinator: str = "and") -> dict[str, Any]:
    return {
        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
        "conditions": conds,
        "combinator": combinator,
    }


# =====================================================================================
# Core nodes
# =====================================================================================

def manual_trigger(wf: Workflow, name: str = "Manual Trigger") -> Node:
    return wf.add(name, "n8n-nodes-base.manualTrigger", 1, {})


def webhook(wf: Workflow, name: str, path: str, method: str = "POST", response: str = "responseNode",
            auth: str | None = None, raw_body: bool = False, binary: bool = False) -> Node:
    """response: onReceived | lastNode | responseNode. auth: None | 'header' (uses CREDS['header'])."""
    params: dict[str, Any] = {"httpMethod": method, "path": path, "responseMode": response, "options": {}}
    if raw_body:
        params["options"]["rawBody"] = True
    if binary:
        params["options"]["binaryPropertyName"] = "data"
    cred = None
    if auth == "header":
        params["authentication"] = "headerAuth"
        cred = "header"
    return wf.add(name, "n8n-nodes-base.webhook", 2, params, cred=cred, webhookId=uid(wf.id, name, "webhook"))


def respond(wf: Workflow, name: str, body: str | dict | None = None, code: int = 200, with_: str = "json",
            headers: dict[str, str] | None = None) -> Node:
    """with_: json | text | noData | firstIncomingItem | allIncomingItems | binary."""
    params: dict[str, Any] = {"respondWith": with_, "options": {}}
    if with_ == "json":
        params["responseBody"] = body if isinstance(body, str) else json.dumps(body or {})
    elif with_ == "text":
        params["responseBody"] = body or ""
    if code != 200:
        params["options"]["responseCode"] = code
    if headers:
        params["options"]["responseHeaders"] = {"entries": [{"name": k, "value": v} for k, v in headers.items()]}
    return wf.add(name, "n8n-nodes-base.respondToWebhook", 1.1, params)


def schedule(wf: Workflow, name: str = "Schedule", *, cron: str | None = None, minutes: int | None = None,
             hours: int | None = None, daily_at: tuple[int, int] | None = None, seconds: int | None = None) -> Node:
    if cron:
        interval = {"field": "cronExpression", "expression": cron}
    elif seconds:
        interval = {"field": "seconds", "secondsInterval": seconds}
    elif minutes:
        interval = {"field": "minutes", "minutesInterval": minutes}
    elif hours:
        interval = {"field": "hours", "hoursInterval": hours}
    elif daily_at:
        interval = {"field": "days", "daysInterval": 1, "triggerAtHour": daily_at[0], "triggerAtMinute": daily_at[1]}
    else:
        interval = {"field": "hours", "hoursInterval": 1}
    return wf.add(name, "n8n-nodes-base.scheduleTrigger", 1.2, {"rule": {"interval": [interval]}})


def form_trigger(wf: Workflow, name: str, path: str, title: str, fields: list[dict], description: str = "",
                 response: str = "onReceived", button: str = "Submit") -> Node:
    """fields: [{"fieldLabel": "Name", "requiredField": True}, {"fieldLabel": "Email", "fieldType": "email"},
    {"fieldLabel": "Topic", "fieldType": "dropdown", "fieldOptions": {"values": [{"option": "A"}]}}]"""
    params = {"formTitle": title, "formDescription": description, "formFields": {"values": fields},
              "responseMode": response, "path": path, "options": {"buttonLabel": button}}
    return wf.add(name, "n8n-nodes-base.formTrigger", 2.2, params, webhookId=uid(wf.id, name, "webhook"))


def imap_trigger(wf: Workflow, name: str = "Email Trigger (IMAP)", mailbox: str = "INBOX", attachments: bool = True) -> Node:
    params = {"mailbox": mailbox, "postProcessAction": "read", "format": "resolved", "downloadAttachments": attachments,
              "options": {"customEmailConfig": "[\"UNSEEN\"]", "forceReconnect": 30}}
    return wf.add(name, "n8n-nodes-base.emailReadImap", 2, params, cred="imap")


def file_trigger(wf: Workflow, name: str, path: str, events: tuple[str, ...] = ("add",), folder: bool = True) -> Node:
    params = {"triggerOn": "folder" if folder else "file", "path": path, "events": list(events),
              "options": {"awaitWriteFinish": True, "ignoreInitial": True, "usePolling": True}}
    return wf.add(name, "n8n-nodes-base.localFileTrigger", 1, params)


def telegram_trigger(wf: Workflow, name: str = "Telegram Trigger") -> Node:
    return wf.add(name, "n8n-nodes-base.telegramTrigger", 1.1, {"updates": ["message"], "additionalFields": {}},
                  cred="telegram", webhookId=uid(wf.id, name, "webhook"))


def telegram_send(wf: Workflow, name: str, chat_id: str, text: str, markdown: bool = True) -> Node:
    params = {"chatId": chat_id, "text": text, "additionalFields": {"appendAttribution": False}}
    if markdown:
        params["additionalFields"]["parse_mode"] = "Markdown"
    return wf.add(name, "n8n-nodes-base.telegram", 1.2, params, cred="telegram")


def chat_trigger(wf: Workflow, name: str = "Chat Trigger", public: bool = True, title: str = "Automation Lab",
                 subtitle: str = "", initial: str = "") -> Node:
    params: dict[str, Any] = {"public": public, "mode": "hostedChat" if public else "test",
                              "options": {"title": title, "subtitle": subtitle, "responseMode": "lastNode"}}
    if initial:
        params["initialMessages"] = initial
    return wf.add(name, "@n8n/n8n-nodes-langchain.chatTrigger", 1.1, params, webhookId=uid(wf.id, name, "webhook"))


def sub_trigger(wf: Workflow, name: str = "When called by another workflow") -> Node:
    return wf.add(name, "n8n-nodes-base.executeWorkflowTrigger", 1.1, {"inputSource": "passthrough"})


def error_trigger(wf: Workflow, name: str = "Error Trigger") -> Node:
    return wf.add(name, "n8n-nodes-base.errorTrigger", 1, {})


def postgres_query(wf: Workflow, name: str, sql: str, params: str | None = None) -> Node:
    """Parameterised query: use $1, $2 in sql and pass params as a comma-separated expression string,
    e.g. params='={{ $json.id }}, {{ $json.email }}'."""
    p: dict[str, Any] = {"operation": "executeQuery", "query": sql, "options": {}}
    if params:
        p["options"]["queryReplacement"] = params
    return wf.add(name, "n8n-nodes-base.postgres", 2.5, p, cred="postgres")


def _mapper(mapping: dict[str, str] | None, matching: list[str] | None = None) -> dict[str, Any]:
    if mapping is None:
        return {"mappingMode": "autoMapInputData", "value": {}, "matchingColumns": matching or [], "schema": [],
                "attemptToConvertTypes": False, "convertFieldsToString": False}
    return {"mappingMode": "defineBelow", "value": mapping, "matchingColumns": matching or [], "schema": [],
            "attemptToConvertTypes": False, "convertFieldsToString": False}


def postgres_insert(wf: Workflow, name: str, table: str, mapping: dict[str, str] | None = None, schema: str = "public",
                    returning: bool = False) -> Node:
    p: dict[str, Any] = {"operation": "insert", "schema": rl(schema), "table": rl(table), "columns": _mapper(mapping),
                         "options": {}}
    if returning:
        p["options"]["outputColumns"] = ["*"]
    return wf.add(name, "n8n-nodes-base.postgres", 2.5, p, cred="postgres")


def postgres_upsert(wf: Workflow, name: str, table: str, match: list[str], mapping: dict[str, str] | None = None,
                    schema: str = "public") -> Node:
    p = {"operation": "upsert", "schema": rl(schema), "table": rl(table), "columns": _mapper(mapping, match),
         "options": {}}
    return wf.add(name, "n8n-nodes-base.postgres", 2.5, p, cred="postgres")


def postgres_select(wf: Workflow, name: str, table: str, where: dict[str, str] | None = None, limit: int | None = None,
                    schema: str = "public", sort: tuple[str, str] | None = None) -> Node:
    p: dict[str, Any] = {"operation": "select", "schema": rl(schema), "table": rl(table), "options": {}}
    if where:
        p["where"] = {"values": [{"column": k, "condition": "equal", "value": v} for k, v in where.items()]}
    if sort:
        p["sort"] = {"values": [{"column": sort[0], "direction": sort[1].upper()}]}
    if limit:
        p["limit"] = limit
    else:
        p["returnAll"] = True
    return wf.add(name, "n8n-nodes-base.postgres", 2.5, p, cred="postgres")


def set_fields(wf: Workflow, name: str, fields: dict[str, Any], include_other: bool = False) -> Node:
    """fields: {"name": "={{ $json.x }}", "count": 3, "flag": True, "obj": {"a": 1}}  (type inferred).
    Pass a tuple (value, type) to force a type, e.g. ("={{ $json.n }}", "number")."""
    assignments = []
    for k, v in fields.items():
        if isinstance(v, bool):
            t, val = "boolean", v
        elif isinstance(v, (int, float)):
            t, val = "number", v
        elif isinstance(v, (dict, list)):
            t, val = "object" if isinstance(v, dict) else "array", "=" + json.dumps(v)
        elif isinstance(v, tuple):
            val, t = v
        else:
            t, val = "string", v
        assignments.append({"id": uid(wf.id, name, k), "name": k, "value": val, "type": t})
    params: dict[str, Any] = {"assignments": {"assignments": assignments}, "options": {}}
    if include_other:
        params["includeOtherFields"] = True
    return wf.add(name, "n8n-nodes-base.set", 3.4, params)


def code(wf: Workflow, name: str, js: str, per_item: bool = False) -> Node:
    params: dict[str, Any] = {"jsCode": js.strip("\n")}
    if per_item:
        params["mode"] = "runOnceForEachItem"
    return wf.add(name, "n8n-nodes-base.code", 2, params)


def if_(wf: Workflow, name: str, conds: list[dict], combinator: str = "and") -> Node:
    return wf.add(name, "n8n-nodes-base.if", 2.2, {"conditions": _conditions(conds, combinator), "options": {}})


def filter_(wf: Workflow, name: str, conds: list[dict], combinator: str = "and") -> Node:
    return wf.add(name, "n8n-nodes-base.filter", 2.2, {"conditions": _conditions(conds, combinator), "options": {}})


def switch(wf: Workflow, name: str, rules: list[tuple[str, list[dict]]], fallback: str = "extra",
           combinator: str = "and") -> Node:
    """rules: [(outputKey, [conditions...]), ...]. Outputs are in rule order; fallback 'extra' adds a last output."""
    values = [{"conditions": _conditions(c, combinator), "renameOutput": True, "outputKey": key} for key, c in rules]
    return wf.add(name, "n8n-nodes-base.switch", 3.2,
                  {"rules": {"values": values}, "options": {"fallbackOutput": fallback}})


def http(wf: Workflow, name: str, url: str, method: str = "GET", *, query: dict[str, str] | None = None,
         headers: dict[str, str] | None = None, json_body: str | dict | None = None,
         form_binary: tuple[str, str] | None = None, full_response: bool = False, never_error: bool = False,
         timeout_ms: int | None = None, response: str = "json", batching: tuple[int, int] | None = None,
         auth_cred: str | None = None, pagination: dict | None = None) -> Node:
    params: dict[str, Any] = {"method": method, "url": url, "options": {}}
    if query:
        params["sendQuery"] = True
        params["queryParameters"] = {"parameters": [{"name": k, "value": v} for k, v in query.items()]}
    if headers:
        params["sendHeaders"] = True
        params["headerParameters"] = {"parameters": [{"name": k, "value": v} for k, v in headers.items()]}
    if json_body is not None:
        params["sendBody"] = True
        params["specifyBody"] = "json"
        params["jsonBody"] = json_body if isinstance(json_body, str) else "=" + json.dumps(json_body)
    if form_binary:
        field, prop = form_binary
        params["sendBody"] = True
        params["contentType"] = "multipart-form-data"
        params["bodyParameters"] = {"parameters": [{"parameterType": "formBinaryData", "name": field,
                                                    "inputDataFieldName": prop}]}
    resp: dict[str, Any] = {}
    if full_response:
        resp["fullResponse"] = True
    if never_error:
        resp["neverError"] = True
    if response != "json":
        resp["responseFormat"] = response  # text | file | autodetect
        if response == "file":
            resp["outputPropertyName"] = "data"
    if resp:
        params["options"]["response"] = {"response": resp}
    if timeout_ms:
        params["options"]["timeout"] = timeout_ms
    if batching:
        params["options"]["batching"] = {"batch": {"batchSize": batching[0], "batchInterval": batching[1]}}
    if pagination:
        params["options"]["pagination"] = {"pagination": pagination}
    cred = None
    if auth_cred:
        if auth_cred == "header":
            params["authentication"] = "genericCredentialType"
            params["genericAuthType"] = "httpHeaderAuth"
        else:
            params["authentication"] = "predefinedCredentialType"
            params["nodeCredentialType"] = CREDS[auth_cred][0]
        cred = auth_cred
    return wf.add(name, "n8n-nodes-base.httpRequest", 4.2, params, cred=cred)


def email(wf: Workflow, name: str, to: str, subject: str, *, html: str | None = None, text: str | None = None,
          from_: str = "automation-lab@lab.local", attachments: str | None = None, cc: str | None = None) -> Node:
    params: dict[str, Any] = {"fromEmail": from_, "toEmail": to, "subject": subject, "options": {}}
    if html is not None:
        params["emailFormat"] = "html"
        params["html"] = html
    else:
        params["emailFormat"] = "text"
        params["text"] = text or ""
    if attachments:
        params["options"]["attachments"] = attachments
    if cc:
        params["options"]["ccEmail"] = cc
    return wf.add(name, "n8n-nodes-base.emailSend", 2.1, params, cred="smtp")


def redis(wf: Workflow, name: str, operation: str, key: str, value: str | None = None, *, ttl: int | None = None,
          key_type: str = "automatic", prop: str = "value") -> Node:
    """operation: get | set | incr | delete | keys | push | pop | publish"""
    params: dict[str, Any] = {"operation": operation, "key": key}
    if operation == "get":
        params["propertyName"] = prop
        params["keyType"] = key_type
        params["options"] = {}
    elif operation == "set":
        params["value"] = value or ""
        params["keyType"] = "string" if key_type == "automatic" else key_type
        if ttl:
            params.update(expire=True, ttl=ttl)
    elif operation == "incr":
        if ttl:
            params.update(expire=True, ttl=ttl)
    elif operation == "keys":
        params["keyPattern"] = key
        params["getValues"] = True
        del params["key"]
    return wf.add(name, "n8n-nodes-base.redis", 1, params, cred="redis")


def s3_upload(wf: Workflow, name: str, bucket: str, file_name: str, prop: str = "data") -> Node:
    params = {"resource": "file", "operation": "upload", "bucketName": bucket, "fileName": file_name,
              "binaryData": True, "binaryPropertyName": prop, "additionalFields": {}, "tagsUi": {}}
    return wf.add(name, "n8n-nodes-base.s3", 1, params, cred="s3")


def s3_list(wf: Workflow, name: str, bucket: str, prefix: str | None = None) -> Node:
    params: dict[str, Any] = {"resource": "file", "operation": "getAll", "bucketName": bucket, "returnAll": True,
                              "options": {}}
    if prefix:
        params["options"]["prefix"] = prefix
    return wf.add(name, "n8n-nodes-base.s3", 1, params, cred="s3")


def s3_delete(wf: Workflow, name: str, bucket: str, key: str) -> Node:
    params = {"resource": "file", "operation": "delete", "bucketName": bucket, "fileKey": key, "options": {}}
    return wf.add(name, "n8n-nodes-base.s3", 1, params, cred="s3")


def read_file(wf: Workflow, name: str, selector: str, prop: str = "data") -> Node:
    return wf.add(name, "n8n-nodes-base.readWriteFile", 1,
                  {"fileSelector": selector, "options": {"dataPropertyName": prop}})


def write_file(wf: Workflow, name: str, file_name: str, prop: str = "data", append: bool = False) -> Node:
    params: dict[str, Any] = {"operation": "write", "fileName": file_name, "dataPropertyName": prop, "options": {}}
    if append:
        params["options"]["append"] = True
    return wf.add(name, "n8n-nodes-base.readWriteFile", 1, params)


def extract(wf: Workflow, name: str, operation: str, prop: str = "data", **options: Any) -> Node:
    """operation: csv | xlsx | xls | pdf | text | html | fromJson | binaryToPropery | ods | rtf"""
    return wf.add(name, "n8n-nodes-base.extractFromFile", 1,
                  {"operation": operation, "binaryPropertyName": prop, "options": options})


def convert(wf: Workflow, name: str, operation: str, file_name: str | None = None, prop: str = "data",
            source: str = "data", **options: Any) -> Node:
    """operation: csv | xlsx | json | text | html | toBinary | iCal | rtf"""
    opts = dict(options)
    if file_name:
        opts["fileName"] = file_name
    params: dict[str, Any] = {"operation": operation, "options": opts}
    if operation == "toBinary":
        params["sourceProperty"] = source
    if prop != "data":
        params["binaryPropertyName"] = prop
    return wf.add(name, "n8n-nodes-base.convertToFile", 1.1, params)


def execute_workflow(wf: Workflow, name: str, workflow_id: str, mode: str = "once", wait: bool = True,
                     cached_name: str | None = None) -> Node:
    """mode: once (all items in one call) | each (one call per item)."""
    params = {"source": "database", "workflowId": rl(workflow_id, "id", cached_name), "mode": mode,
              "options": {"waitForSubWorkflow": wait}}
    return wf.add(name, "n8n-nodes-base.executeWorkflow", 1.1, params)


def stop_error(wf: Workflow, name: str, message: str) -> Node:
    return wf.add(name, "n8n-nodes-base.stopAndError", 1, {"errorMessage": message})


def wait(wf: Workflow, name: str, amount: float | None = None, unit: str = "minutes", until: str | None = None,
         resume_webhook: bool = False) -> Node:
    if resume_webhook:
        params: dict[str, Any] = {"resume": "webhook", "options": {}}
    elif until:
        params = {"resume": "specificTime", "dateTime": until}
    else:
        params = {"amount": amount or 1, "unit": unit}
    return wf.add(name, "n8n-nodes-base.wait", 1.1, params, webhookId=uid(wf.id, name, "webhook"))


def merge(wf: Workflow, name: str, mode: str = "append", *, by: str | None = None,
          fields: list[tuple[str, str]] | None = None, join: str = "keepMatches", inputs: int = 2) -> Node:
    """mode: append | combine | chooseBranch. combine by: position | fields | all."""
    params: dict[str, Any] = {"mode": mode, "options": {}}
    if mode == "combine":
        params["combineBy"] = {"position": "combineByPosition", "fields": "combineByFields",
                               "all": "combineAll"}[by or "position"]
        if by == "fields" and fields:
            params["advanced"] = True
            params["mergeByFields"] = {"values": [{"field1": a, "field2": b} for a, b in fields]}
            params["joinMode"] = join
    if inputs != 2:
        params["numberInputs"] = inputs
    return wf.add(name, "n8n-nodes-base.merge", 3, params)


def loop(wf: Workflow, name: str = "Loop Over Items", batch_size: int = 10) -> Node:
    """Split In Batches v3: output 0 = done, output 1 = loop. Connect the loop body back into this node."""
    return wf.add(name, "n8n-nodes-base.splitInBatches", 3, {"batchSize": batch_size, "options": {}})


def aggregate(wf: Workflow, name: str, fields: list[str] | None = None, output: str = "data") -> Node:
    if fields:
        params: dict[str, Any] = {"aggregate": "aggregateIndividualFields",
                                  "fieldsToAggregate": {"fieldToAggregate": [{"fieldToAggregate": f} for f in fields]},
                                  "options": {}}
    else:
        params = {"aggregate": "aggregateAllItemData", "destinationFieldName": output, "options": {}}
    return wf.add(name, "n8n-nodes-base.aggregate", 1, params)


def split_out(wf: Workflow, name: str, field: str, include: str | None = None) -> Node:
    params: dict[str, Any] = {"fieldToSplitOut": field, "options": {}}
    if include:
        params["include"] = include  # noOtherFields | allOtherFields | selectedOtherFields
    return wf.add(name, "n8n-nodes-base.splitOut", 1, params)


def sort(wf: Workflow, name: str, fields: list[tuple[str, str]]) -> Node:
    return wf.add(name, "n8n-nodes-base.sort", 1,
                  {"sortFieldsUi": {"sortField": [{"fieldName": f, "order": o} for f, o in fields]}, "options": {}})


def limit(wf: Workflow, name: str, max_items: int, keep: str = "firstItems") -> Node:
    return wf.add(name, "n8n-nodes-base.limit", 1, {"maxItems": max_items, "keep": keep})


def dedupe(wf: Workflow, name: str, fields: list[str] | None = None) -> Node:
    if fields:
        params: dict[str, Any] = {"compare": "selectedFields", "fieldsToCompare": ", ".join(fields), "options": {}}
    else:
        params = {"compare": "allFields", "options": {}}
    return wf.add(name, "n8n-nodes-base.removeDuplicates", 1.1, params)


def dedupe_prev_runs(wf: Workflow, name: str, key_expr: str, scope: str = "workflow", history: int = 10000) -> Node:
    params = {"operation": "removeItemsSeenInPreviousExecutions", "logic": "removeItemsWithAlreadySeenKeyValues",
              "dedupeValue": key_expr, "options": {"scope": scope, "historySize": history}}
    return wf.add(name, "n8n-nodes-base.removeDuplicates", 2, params)


def crypto_hash(wf: Workflow, name: str, value: str, prop: str = "hash", algo: str = "SHA256",
                hmac_secret: str | None = None) -> Node:
    if hmac_secret is not None:
        params: dict[str, Any] = {"action": "hmac", "type": algo, "value": value, "dataPropertyName": prop,
                                  "secret": hmac_secret, "encoding": "hex"}
    else:
        params = {"action": "hash", "type": algo, "value": value, "dataPropertyName": prop, "encoding": "hex"}
    return wf.add(name, "n8n-nodes-base.crypto", 1, params)


def html_extract(wf: Workflow, name: str, values: list[dict], source: str = "json", prop: str = "data") -> Node:
    """values: [{"key": "title", "cssSelector": "h2", "returnValue": "text", "returnArray": False}, ...]"""
    params = {"operation": "extractHtmlContent", "sourceData": source, "dataPropertyName": prop,
              "extractionValues": {"values": values}, "options": {}}
    return wf.add(name, "n8n-nodes-base.html", 1.2, params)


def rss(wf: Workflow, name: str, url: str) -> Node:
    return wf.add(name, "n8n-nodes-base.rssFeedRead", 1.1, {"url": url, "options": {}})


def compress(wf: Workflow, name: str, file_name: str, prop: str = "data", fmt: str = "zip") -> Node:
    return wf.add(name, "n8n-nodes-base.compression", 1.1,
                  {"operation": "compress", "binaryPropertyName": prop, "outputFormat": fmt, "fileName": file_name})


def decompress(wf: Workflow, name: str, prop: str = "data") -> Node:
    return wf.add(name, "n8n-nodes-base.compression", 1.1,
                  {"operation": "decompress", "binaryPropertyName": prop, "outputPrefix": "file_"})


def markdown(wf: Workflow, name: str, value: str, to_html: bool = True, dest: str = "html") -> Node:
    if to_html:
        return wf.add(name, "n8n-nodes-base.markdown", 1,
                      {"mode": "markdownToHtml", "markdown": value, "destinationKey": dest, "options": {}})
    return wf.add(name, "n8n-nodes-base.markdown", 1, {"html": value, "destinationKey": dest, "options": {}})


def noop(wf: Workflow, name: str = "No Operation") -> Node:
    return wf.add(name, "n8n-nodes-base.noOp", 1, {})


def n8n_api(wf: Workflow, name: str, resource: str, operation: str = "getAll", *, filters: dict | None = None,
            return_all: bool = False, limit_: int = 100, workflow_id: str | None = None) -> Node:
    params: dict[str, Any] = {"resource": resource, "operation": operation}
    if operation == "getAll":
        params["returnAll"] = return_all
        if not return_all:
            params["limit"] = limit_
        params["filters"] = filters or {}
    if workflow_id:
        params["workflowId"] = rl(workflow_id, "id")
    return wf.add(name, "n8n-nodes-base.n8n", 1, params, cred="n8n")


def rename_keys(wf: Workflow, name: str, mapping: dict[str, str]) -> Node:
    return wf.add(name, "n8n-nodes-base.renameKeys", 1,
                  {"keys": {"key": [{"currentKey": k, "newKey": v} for k, v in mapping.items()]},
                   "additionalOptions": {}})


def date_time(wf: Workflow, name: str, date: str, fmt: str = "yyyy-MM-dd", output: str = "formatted") -> Node:
    return wf.add(name, "n8n-nodes-base.dateTime", 2,
                  {"operation": "formatDate", "date": date, "format": "custom", "customFormat": fmt,
                   "outputFieldName": output, "options": {}})


def xml(wf: Workflow, name: str, mode: str = "jsonToxml", prop: str = "data") -> Node:
    return wf.add(name, "n8n-nodes-base.xml", 1, {"mode": mode, "dataPropertyName": prop, "options": {}})


# =====================================================================================
# LangChain (all local: Ollama + Qdrant)
# =====================================================================================

def ollama_chat(wf: Workflow, name: str = "Ollama Chat Model", model: str = "llama3.2:3b", temperature: float = 0.2,
                json_mode: bool = False) -> Node:
    opts: dict[str, Any] = {"temperature": temperature}
    if json_mode:
        opts["format"] = "json"
    return wf.add(name, "@n8n/n8n-nodes-langchain.lmChatOllama", 1, {"model": model, "options": opts}, cred="ollama")


def ollama_embed(wf: Workflow, name: str = "Embeddings Ollama", model: str = "nomic-embed-text:latest") -> Node:
    return wf.add(name, "@n8n/n8n-nodes-langchain.embeddingsOllama", 1, {"model": model}, cred="ollama")


def qdrant_store(wf: Workflow, name: str, collection: str, mode: str = "insert", top_k: int = 4) -> Node:
    """mode: insert | load | retrieve | retrieve-as-tool"""
    params: dict[str, Any] = {"mode": mode, "qdrantCollection": rl(collection, "id"), "options": {}}
    if mode in ("load", "retrieve-as-tool"):
        params["topK"] = top_k
    if mode == "load":
        params["prompt"] = "={{ $json.chatInput }}"
    return wf.add(name, "@n8n/n8n-nodes-langchain.vectorStoreQdrant", 1, params, cred="qdrant")


def doc_loader(wf: Workflow, name: str = "Default Data Loader", binary: bool = True, loader: str = "auto") -> Node:
    params: dict[str, Any] = {"dataType": "binary" if binary else "json", "loader": loader,
                              "binaryMode": "allInputData", "options": {}}
    if not binary:
        params["jsonMode"] = "allInputData"
    return wf.add(name, "@n8n/n8n-nodes-langchain.documentDefaultDataLoader", 1, params)


def text_splitter(wf: Workflow, name: str = "Recursive Character Text Splitter", size: int = 800,
                  overlap: int = 100) -> Node:
    return wf.add(name, "@n8n/n8n-nodes-langchain.textSplitterRecursiveCharacterTextSplitter", 1,
                  {"chunkSize": size, "chunkOverlap": overlap, "options": {}})


def llm_chain(wf: Workflow, name: str, prompt: str, system: str | None = None, parser: bool = False) -> Node:
    params: dict[str, Any] = {"promptType": "define", "text": prompt, "hasOutputParser": parser}
    if system:
        params["messages"] = {"messageValues": [{"message": system}]}
    return wf.add(name, "@n8n/n8n-nodes-langchain.chainLlm", 1.4, params)


def output_parser(wf: Workflow, name: str = "Structured Output Parser", example: dict | None = None,
                  schema: dict | None = None) -> Node:
    if schema is not None:
        params: dict[str, Any] = {"schemaType": "manual", "inputSchema": json.dumps(schema, indent=2)}
    else:
        params = {"schemaType": "fromJson", "jsonSchemaExample": json.dumps(example or {}, indent=2)}
    return wf.add(name, "@n8n/n8n-nodes-langchain.outputParserStructured", 1.2, params)


def agent(wf: Workflow, name: str, system: str, prompt: str = "={{ $json.chatInput }}", max_iterations: int = 8) -> Node:
    params = {"promptType": "define", "text": prompt,
              "options": {"systemMessage": system, "maxIterations": max_iterations}}
    return wf.add(name, "@n8n/n8n-nodes-langchain.agent", 2, params)


def memory(wf: Workflow, name: str = "Window Buffer Memory", window: int = 10, session_key: str | None = None) -> Node:
    params: dict[str, Any] = {"contextWindowLength": window}
    if session_key:
        params.update(sessionIdType="customKey", sessionKey=session_key)
    return wf.add(name, "@n8n/n8n-nodes-langchain.memoryBufferWindow", 1.3, params)


def tool_workflow(wf: Workflow, name: str, workflow_id: str, description: str, example: dict,
                  cached_name: str | None = None) -> Node:
    params = {"name": re.sub(r"[^a-zA-Z0-9_]", "_", name.lower()), "description": description, "source": "database",
              "workflowId": rl(workflow_id, "id", cached_name), "specifyInputSchema": True, "schemaType": "fromJson",
              "jsonSchemaExample": json.dumps(example, indent=2), "fields": {"values": []}}
    return wf.add(name, "@n8n/n8n-nodes-langchain.toolWorkflow", 1.3, params)


def tool_code(wf: Workflow, name: str, description: str, js: str, example: dict | None = None) -> Node:
    params: dict[str, Any] = {"name": re.sub(r"[^a-zA-Z0-9_]", "_", name.lower()), "description": description,
                              "jsCode": js.strip("\n")}
    if example:
        params.update(specifyInputSchema=True, schemaType="fromJson", jsonSchemaExample=json.dumps(example, indent=2))
    return wf.add(name, "@n8n/n8n-nodes-langchain.toolCode", 1.1, params)


def info_extractor(wf: Workflow, name: str, text: str, example: dict, system: str | None = None) -> Node:
    params: dict[str, Any] = {"text": text, "schemaType": "fromJson", "jsonSchemaExample": json.dumps(example, indent=2),
                              "options": {}}
    if system:
        params["options"]["systemPromptTemplate"] = system
    return wf.add(name, "@n8n/n8n-nodes-langchain.informationExtractor", 1, params)


def text_classifier(wf: Workflow, name: str, text: str, categories: list[tuple[str, str]], fallback: str = "other") -> Node:
    """Outputs are indexed in `categories` order; fallback 'other' adds a final output, 'discard' drops unmatched."""
    params = {"inputText": text, "categories": {"categories": [{"category": c, "description": d} for c, d in categories]},
              "options": {"fallback": fallback}}
    return wf.add(name, "@n8n/n8n-nodes-langchain.textClassifier", 1, params)


def retriever(wf: Workflow, name: str = "Vector Store Retriever", top_k: int = 4) -> Node:
    return wf.add(name, "@n8n/n8n-nodes-langchain.retrieverVectorStore", 1, {"topK": top_k})


def qa_chain(wf: Workflow, name: str, query: str = "={{ $json.chatInput }}", system: str | None = None) -> Node:
    params: dict[str, Any] = {"promptType": "define", "text": query, "options": {}}
    if system:
        params["options"]["systemPromptTemplate"] = system
    return wf.add(name, "@n8n/n8n-nodes-langchain.chainRetrievalQa", 1.4, params)


def summarize_chain(wf: Workflow, name: str = "Summarize", prompt: str | None = None) -> Node:
    params: dict[str, Any] = {"options": {}}
    if prompt:
        params["options"]["summarizationMethodAndPrompts"] = {
            "values": {"summarizationMethod": "map_reduce", "prompt": prompt, "combineMapPrompt": prompt}}
    return wf.add(name, "@n8n/n8n-nodes-langchain.chainSummarization", 2, params)
