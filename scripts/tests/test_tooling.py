"""Unit tests for the repo tooling (validate, build-matrix, scaffold, render-preview, milestone, changelog).

Run from the repo root:  python -m unittest discover -s scripts/tests -v
Plain unittest, standard library only (the render test is skipped when Pillow is missing).
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import validate  # noqa: E402


def load_script(name: str):
    """Import a hyphenated script (build-matrix.py, render-preview.py) as a module."""
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


build_matrix = load_script("build-matrix")
render_preview = load_script("render-preview")
scaffold = load_script("scaffold")
milestone = load_script("milestone")
changelog = load_script("changelog")

try:
    from PIL import Image  # type: ignore
    HAVE_PIL = True
except ImportError:  # pragma: no cover
    HAVE_PIL = False

# Built at runtime so this file never contains a key-shaped string itself.
FAKE_AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"

WORKFLOW_README = """---
id: {code}
title: {title}
category: {category}
difficulty: {difficulty}
status: {status}
patterns: [{patterns}]
services: [core]
tested_on: n8n 2.37.10
---

# {code} - {title}

**Category:** {category} · **Difficulty:** {difficulty} · **Tested on:** n8n 2.37.10

## Problem

A synthetic scenario for the tooling tests; contact ops@lab.local.

## How it works

1. Trigger
2. Transform
3. Store

![screenshot](assets/screenshot.png)

## Setup

- Services: core

## Try it

```bash
curl -X POST http://localhost:5678/webhook/{code}
```

## Notes & trade-offs

- none
"""

PATTERN_README = """---
id: {code}
title: {title}
category: Patterns
difficulty: Intermediate
status: {status}
patterns: []
services: [core]
tested_on: n8n 2.37.10
---

# {code} - {title}

## Problem

Failures vanish silently.

## Pattern

Route every failure to one handler.

## Implementation in n8n

1. Error Trigger -> log -> notify.

![screenshot](assets/screenshot.png)

## Trade-offs

- one more workflow to maintain

## Used by

- `T01 - Webhook to Database`
"""


def node(name: str, type_: str, version: float, x: int, y: int, params: dict | None = None, **extra) -> dict:
    d = {"parameters": params or {}, "id": f"id-{name.lower().replace(' ', '-')}", "name": name, "type": type_,
         "typeVersion": version, "position": [x, y]}
    d.update(extra)
    return d


def workflow_json(code: str, slug: str, title: str, *, value: str = "hello", cred: dict | None = None,
                  with_ai: bool = False) -> dict:
    cred = cred or {"id": "ALcredPostgresDm", "name": "Postgres - demo"}
    nodes = [
        node("Manual Trigger", "n8n-nodes-base.manualTrigger", 1, 0, 0),
        node("Edit Fields", "n8n-nodes-base.set", 3.4, 260, 0,
             {"assignments": {"assignments": [{"id": "a1", "name": "greeting", "value": value, "type": "string"}]},
              "options": {}}),
        node("Postgres", "n8n-nodes-base.postgres", 2.5, 520, 0, {"operation": "executeQuery", "query": "select 1"},
             credentials={"postgres": cred}, retryOnFail=True, maxTries=3),
        node("Check", "n8n-nodes-base.if", 2.2, 780, 0, {"conditions": {"conditions": []}}),
        node("Done", "n8n-nodes-base.noOp", 1, 1040, -110),
        node("Fail", "n8n-nodes-base.stopAndError", 1, 1040, 110, {"message": "nope"}),
        {"parameters": {"content": f"## {code}\nTest sticky note with some wrapped text that goes on for a while.",
                        "height": 160, "width": 360, "color": 1},
         "id": "sticky-1", "name": "Sticky Note", "type": "n8n-nodes-base.stickyNote", "typeVersion": 1,
         "position": [-300, -220]},
    ]
    connections = {
        "Manual Trigger": {"main": [[{"node": "Edit Fields", "type": "main", "index": 0}]]},
        "Edit Fields": {"main": [[{"node": "Postgres", "type": "main", "index": 0}]]},
        "Postgres": {"main": [[{"node": "Check", "type": "main", "index": 0}]]},
        "Check": {"main": [[{"node": "Done", "type": "main", "index": 0}], [{"node": "Fail", "type": "main", "index": 0}]]},
    }
    if with_ai:
        nodes.append(node("Agent", "@n8n/n8n-nodes-langchain.agent", 2, 1300, 0, {"promptType": "define"}))
        nodes.append(node("Ollama Chat Model", "@n8n/n8n-nodes-langchain.lmChatOllama", 1, 1260, 220, {"model": "llama3.2:3b"}))
        connections["Done"] = {"main": [[{"node": "Agent", "type": "main", "index": 0}]]}
        connections["Ollama Chat Model"] = {"ai_languageModel": [[{"node": "Agent", "type": "ai_languageModel", "index": 0}]]}
    return {
        "name": f"{code} - {title}", "nodes": nodes, "connections": connections, "active": False,
        "settings": {"executionOrder": "v1", "errorWorkflow": validate.wf_id("P01", "error-handler")},
        "pinData": {}, "id": validate.wf_id(code, slug), "tags": [{"name": "automation-lab"}],
    }


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


class Fixture:
    """Temp repo with two workflow folders and one pattern folder."""

    def __init__(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="automation-lab-test-"))
        root = self.dir
        write(root / ".gitignore", ".env\n")
        write(root / "docker-compose.yml", "services: {}\n")
        write(root / "scripts" / "validate.py", "# stub\n")
        write(root / "README.md",
              "# Automation Lab\n\n<!-- STATS:START --><!-- STATS:END -->\n\n## Coverage matrix\n\n"
              "<!-- MATRIX:START -->\n<!-- MATRIX:END -->\n\n## Patterns\n\n<!-- PATTERNS:START -->\n<!-- PATTERNS:END -->\n")
        self.t01 = root / "workflows" / "T01-webhook-to-database"
        self.t02 = root / "workflows" / "T02-scheduled-daily-digest"
        self.p01 = root / "patterns" / "P01-error-handler"
        write(self.t01 / "README.md", WORKFLOW_README.format(code="T01", title="Webhook to Database", category="Triggers",
                                                             difficulty="Beginner", status="in-progress", patterns="P01"))
        write(self.t01 / "workflow.json", json.dumps(workflow_json("T01", "webhook-to-database", "Webhook to Database"), indent=2))
        write(self.t01 / "test" / "payload.json", '{"email": "sample@lab.local"}\n')
        write(self.t02 / "README.md", WORKFLOW_README.format(code="T02", title="Scheduled Daily Digest", category="Triggers",
                                                             difficulty="Beginner", status="in-progress", patterns="P01"))
        write(self.t02 / "workflow.json",
              json.dumps(workflow_json("T02", "scheduled-daily-digest", "Scheduled Daily Digest", with_ai=True), indent=2))
        write(self.t02 / "test" / "payload.json", '{"day": "2026-09-06"}\n')
        write(self.p01 / "README.md", PATTERN_README.format(code="P01", title="Global Error Handler", status="in-progress"))
        p01 = workflow_json("P01", "error-handler", "Global Error Handler")
        p01["settings"].pop("errorWorkflow")
        write(self.p01 / "workflow.json", json.dumps(p01, indent=2))
        write(self.p01 / "test" / "input.json", '{"execution": {"id": "1"}}\n')

    def cleanup(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


class ValidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)

    def run_validate(self):
        rep, checked = validate.run(self.fx.dir, [])
        return rep, checked

    def test_clean_fixture_has_no_errors(self):
        rep, checked = self.run_validate()
        self.assertEqual(checked, 3)
        self.assertEqual(rep.errors, [], rep.errors)

    def test_missing_front_matter_key(self):
        readme = self.fx.t02 / "README.md"
        text = readme.read_text(encoding="utf-8").replace("difficulty: Beginner\n", "")
        write(readme, text)
        rep, _ = self.run_validate()
        self.assertTrue(any("front-matter missing 'difficulty'" in e for e in rep.errors), rep.errors)

    def test_credential_with_data_key(self):
        wf = workflow_json("T02", "scheduled-daily-digest", "Scheduled Daily Digest",
                           cred={"id": "ALcredPostgresDm", "name": "Postgres - demo", "data": {"password": "x"}})
        write(self.fx.t02 / "workflow.json", json.dumps(wf))
        rep, _ = self.run_validate()
        self.assertTrue(any("must only carry id/name" in e for e in rep.errors), rep.errors)

    def test_env_in_workflow(self):
        wf = workflow_json("T02", "scheduled-daily-digest", "Scheduled Daily Digest", value="={{ $env.API_TOKEN }}")
        write(self.fx.t02 / "workflow.json", json.dumps(wf))
        rep, _ = self.run_validate()
        self.assertTrue(any("$env" in e for e in rep.errors), rep.errors)

    def test_fake_aws_key_in_readme(self):
        readme = self.fx.t01 / "README.md"
        write(readme, readme.read_text(encoding="utf-8") + f"\nkey: {FAKE_AWS_KEY}\n")
        rep, _ = self.run_validate()
        self.assertTrue(any("AWS access key" in e for e in rep.errors), rep.errors)


class BuildMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)

    def test_catalog_has_44_ids(self):
        self.assertEqual(len(build_matrix.PLANNED), 44)
        self.assertEqual(build_matrix.TOTAL_WORKFLOWS, 36)
        self.assertEqual(build_matrix.TOTAL_PATTERNS, 8)

    def test_rows_for_all_catalog_ids(self):
        items = build_matrix.build_catalog(self.fx.dir)
        text = build_matrix.render_all(items)
        for cid in build_matrix.PLANNED:
            self.assertIn(f"| {cid} |", text, f"missing row for {cid}")
        self.assertIn("[Webhook to Database](workflows/T01-webhook-to-database/)", text)
        self.assertIn("[P01](patterns/P01-error-handler/)", text)
        self.assertIn("building", text)
        self.assertIn("0 of 36 workflows shipped · 0 of 8 patterns", text)
        # patterns table: Used by computed from front-matter
        self.assertIn("| P01 | [Global Error Handler](patterns/P01-error-handler/) | [T01]", text)
        for cat in build_matrix.CATEGORY_ORDER:
            self.assertIn(f"### {cat}", text)

    def test_readme_update_and_check(self):
        readme = self.fx.dir / "README.md"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(build_matrix.main(["--root", str(self.fx.dir), "--check"]), 1)  # out of date
            self.assertEqual(build_matrix.main(["--root", str(self.fx.dir)]), 0)
            self.assertEqual(build_matrix.main(["--root", str(self.fx.dir), "--check"]), 0)  # now current
        text = readme.read_text(encoding="utf-8")
        self.assertIn("<!-- MATRIX:START -->\n### Triggers", text)
        self.assertIn("<!-- STATS:START -->**0 of 36 workflows shipped", text)
        self.assertNotIn("<!-- STATS:START -->\n", text)  # stats block stays inline

    def test_missing_readme_prints_tables(self):
        (self.fx.dir / "README.md").unlink()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(build_matrix.main(["--root", str(self.fx.dir)]), 0)
        self.assertIn("| T01 |", buf.getvalue())
        self.assertFalse((self.fx.dir / "README.md").exists())

    def test_used_by_mismatch_warning(self):
        readme = self.fx.p01 / "README.md"
        write(readme, readme.read_text(encoding="utf-8").replace("- `T01 - Webhook to Database`", "- `D04 - Something`"))
        warnings = build_matrix.pattern_warnings(build_matrix.build_catalog(self.fx.dir))
        self.assertTrue(any("does not list T01" in w for w in warnings), warnings)
        self.assertTrue(any("lists D04" in w for w in warnings), warnings)


class ScaffoldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)

    def test_creates_expected_files(self):
        created, errors = scaffold.scaffold(self.fx.dir, "D01", "csv-import", "CSV Import", patterns=["P01"],
                                            services=["core"])
        self.assertEqual(errors, [])
        folder = self.fx.dir / "workflows" / "D01-csv-import"
        for rel in ("README.md", "test/README.md", "test/payload.json", "assets/.gitkeep"):
            self.assertTrue((folder / rel).exists(), rel)
        stub = self.fx.dir / ".claude/skills/n8n-workflow-json/authoring/D01_csv_import.py"
        self.assertTrue(stub.exists())
        self.assertIn('Workflow(\'D01\', \'csv-import\', \'CSV Import\'', stub.read_text(encoding="utf-8"))
        readme = (folder / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("__", readme)
        fm = validate.parse_front_matter(readme)
        self.assertEqual(fm["id"], "D01")
        self.assertEqual(fm["category"], "Data & ETL")
        self.assertEqual(fm["difficulty"], "Beginner")
        self.assertEqual(fm["patterns"], ["P01"])
        self.assertIn("workflows/D01-csv-import", readme)
        self.assertIn("webhook/d01-csv-import", readme)
        self.assertEqual(len(created), 5)  # README, assets/.gitkeep, test/payload.json, test/README.md, stub
        # the new folder passes the contract except for the not-yet-generated workflow.json
        rep, _ = validate.run(self.fx.dir, ["workflows/D01-csv-import"])
        self.assertTrue(all("workflow.json is missing" in e for e in rep.errors), rep.errors)

    def test_pattern_scaffold_and_positional_form(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = scaffold.main(["P03", "idempotency", "Idempotency", "Patterns", "Intermediate", "--root", str(self.fx.dir)])
        self.assertEqual(rc, 0, buf.getvalue())
        folder = self.fx.dir / "patterns" / "P03-idempotency"
        self.assertTrue((folder / "test" / "input.json").exists())
        self.assertTrue((folder / "test" / "expected.json").exists())
        fm = validate.parse_front_matter((folder / "README.md").read_text(encoding="utf-8"))
        self.assertEqual(fm["category"], "Patterns")

    def test_refuses_to_overwrite(self):
        _, errors = scaffold.scaffold(self.fx.dir, "T01", "webhook-to-database", "Webhook to Database")
        self.assertTrue(errors and "already exists" in errors[0], errors)
        _, errors = scaffold.scaffold(self.fx.dir, "T01", "another-slug", "Other")
        self.assertTrue(any("already exists as workflows/T01-webhook-to-database" in e for e in errors), errors)
        created, errors = scaffold.scaffold(self.fx.dir, "T01", "webhook-to-database", "Webhook to Database", force=True)
        self.assertEqual(errors, [])
        self.assertTrue(created)

    def test_rejects_bad_input(self):
        _, errors = scaffold.scaffold(self.fx.dir, "X99", "bad", "Bad")
        self.assertTrue(errors)
        _, errors = scaffold.scaffold(self.fx.dir, "T09", "Bad_Slug", "Bad")
        self.assertTrue(errors)
        _, errors = scaffold.scaffold(self.fx.dir, "T09", "ok", "Ok", category="AI")
        self.assertTrue(any("does not match the id letter" in e for e in errors), errors)


@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class RenderPreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)

    def test_renders_wide_rgb_png_with_marker(self):
        ok, msg = render_preview.render_folder(self.fx.t02, self.fx.dir)
        self.assertTrue(ok, msg)
        shot = self.fx.t02 / "assets" / "screenshot.png"
        self.assertTrue(shot.exists())
        with Image.open(shot) as im:
            self.assertGreaterEqual(im.width, 1400)
            self.assertEqual(im.mode, "RGB")
            self.assertEqual(im.info.get("Software"), render_preview.SOFTWARE_TAG)
        self.assertTrue(render_preview.is_auto_preview(shot))
        self.assertGreaterEqual(validate.png_size(shot)[0], 1400)  # validate's header reader agrees
        # re-render overwrites our own preview without --force
        ok, msg = render_preview.render_folder(self.fx.t02, self.fx.dir)
        self.assertTrue(ok and "wrote" in msg, msg)

    def test_keeps_real_capture_unless_forced(self):
        shot = self.fx.t01 / "assets" / "screenshot.png"
        shot.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1500, 900), (1, 2, 3)).save(shot)
        self.assertFalse(render_preview.is_auto_preview(shot))
        ok, msg = render_preview.render_folder(self.fx.t01, self.fx.dir)
        self.assertTrue(ok and "kept existing" in msg, msg)
        self.assertFalse(render_preview.is_auto_preview(shot))
        ok, msg = render_preview.render_folder(self.fx.t01, self.fx.dir, force=True)
        self.assertTrue(ok and "wrote" in msg, msg)
        self.assertTrue(render_preview.is_auto_preview(shot))

    def test_cli_all_and_id_resolution(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(render_preview.main(["--all", "--root", str(self.fx.dir)]), 0)
            self.assertEqual(render_preview.main(["P01", "--root", str(self.fx.dir)]), 0)
            self.assertEqual(render_preview.main(["Z99", "--root", str(self.fx.dir)]), 1)
        for folder in (self.fx.t01, self.fx.t02, self.fx.p01):
            self.assertTrue((folder / "assets" / "screenshot.png").exists(), folder.name)


class MilestoneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)

    def test_json_report(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(milestone.main(["--root", str(self.fx.dir), "--json", "--next", "6"]), 0)
        data = json.loads(buf.getvalue())
        codes = [m["code"] for m in data["milestones"]]
        self.assertEqual(codes, ["M0", "M1", "M2", "M3", "M4", "M5"])
        m1 = {i["id"]: i["status"] for i in data["milestones"][1]["items"]}
        self.assertEqual(m1["P01"], "in-progress")
        self.assertEqual(m1["T03"], "missing")
        m0 = {i["id"]: i["status"] for i in data["milestones"][0]["items"]}
        self.assertEqual(m0["compose stack"], "shipped")
        self.assertEqual(m0["CI validate workflow"], "missing")
        self.assertEqual([g["code"] for g in data["goals"]], ["G1", "G2", "G3", "G4", "G5", "G6"])
        nxt = [i["id"] for i in data["next"]]
        self.assertEqual(nxt[0], "P01")  # dependency of T01 comes first
        self.assertLess(nxt.index("P02"), nxt.index("T03"))
        self.assertEqual(len(nxt), 6)
        # every milestone item is a catalog id, and the union covers all 44
        all_ids = {i["id"] for m in data["milestones"][1:] for i in m["items"]}
        self.assertEqual(all_ids, set(build_matrix.PLANNED))

    def test_human_report_runs(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(milestone.main(["--root", str(self.fx.dir)]), 0)
        out = buf.getvalue()
        self.assertIn("M1 - Core loop", out)
        self.assertIn("Next items to build", out)


class ChangelogTests(unittest.TestCase):
    def test_parse_and_render(self):
        commits = [
            changelog.parse_commit("a" * 40, "feat(T01): webhook to database", ""),
            changelog.parse_commit("b" * 40, "fix: broken import", "BREAKING CHANGE: ids changed"),
            changelog.parse_commit("c" * 40, "docs: README matrix", ""),
            changelog.parse_commit("d" * 40, "ci: add validate job", ""),
            changelog.parse_commit("e" * 40, "chore!: bump n8n", ""),
            changelog.parse_commit("f" * 40, "refactor: tidy", ""),
            changelog.parse_commit("0" * 40, "random commit message", ""),
        ]
        self.assertEqual(commits[0]["scope"], "T01")
        self.assertTrue(commits[1]["breaking"] and commits[4]["breaking"])
        self.assertEqual(commits[6]["group"], "other")
        md = changelog.render(commits, version="v0.2.0", since="v0.1.0", url="https://github.com/acme/lab", date="2026-09-06")
        for heading in ("## v0.2.0 (2026-09-06)", "### Breaking changes", "### Features", "### Bug fixes",
                        "### Documentation", "### CI", "### Chores", "### Other"):
            self.assertIn(heading, md)
        self.assertIn("**T01:** webhook to database ([aaaaaaa](https://github.com/acme/lab/commit/" + "a" * 40 + "))", md)
        self.assertIn("v0.1.0...v0.2.0", md)
        self.assertIn("_No changes._", changelog.render([], version="Unreleased", since=None, url=None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
