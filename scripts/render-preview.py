#!/usr/bin/env python
"""render-preview.py - draw assets/screenshot.png for a workflow folder from workflow.json, no browser needed.

  python scripts/render-preview.py workflows/T01-webhook-to-database   # one folder (or a catalog id: T01)
  python scripts/render-preview.py --all                                # every workflows/* and patterns/* folder
  python scripts/render-preview.py <folder> --force                     # overwrite a real canvas capture too

Output: RGB PNG, >= 1400 px wide. Nodes are laid out by their `position`, drawn as rounded boxes with the node name
and a short type label; `main` connections are grey, `ai_*` lanes are purple and dashed; sticky notes become
panels with wrapped text; a title bar carries the workflow name and id and the footer says the image is auto-rendered.
The PNG gets a tEXt chunk `Software=automation-lab-preview`; an existing screenshot WITHOUT that chunk is treated as a
real n8n capture and is left alone unless --force is given.

Requires Pillow (`pip install pillow`); everything else is standard library.
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
import validate  # noqa: E402

SOFTWARE_TAG = "automation-lab-preview"
MIN_WIDTH = 1400
NODE_W, NODE_H = 180, 76          # canvas units (n8n positions are in canvas units)
MARGIN = 70
TITLE_H, FOOTER_H = 64, 38
MIN_SCALE, MAX_SCALE = 0.75, 1.6

BG = (244, 245, 247)
GRID = (222, 225, 230)
TITLE_BG = (17, 24, 39)
TITLE_FG = (255, 255, 255)
TITLE_MUTED = (156, 163, 175)
FOOTER_FG = (107, 114, 128)
NODE_FILL = (255, 255, 255)
NODE_TEXT = (17, 24, 39)
NODE_SUB = (107, 114, 128)
MAIN_LINE = (120, 128, 140)
AI_LINE = (124, 58, 237)
LANE_TEXT = (75, 85, 99)
COLOR_DEFAULT = (100, 116, 139)
COLOR_TRIGGER = (22, 163, 74)
COLOR_AI = (124, 58, 237)
COLOR_ERROR = (220, 38, 38)
COLOR_IO = (14, 165, 233)
COLOR_FLOW = (37, 99, 235)
STICKY_COLORS = {  # n8n sticky `color` parameter -> (fill, border)
    1: ((255, 245, 178), (240, 214, 96)), 2: ((255, 224, 178), (236, 181, 110)),
    3: ((255, 208, 208), (232, 150, 150)), 4: ((220, 244, 214), (150, 208, 130)),
    5: ((214, 232, 255), (140, 180, 235)), 6: ((232, 218, 255), (180, 150, 230)),
    7: ((250, 250, 250), (200, 200, 200)),
}
IO_TYPES = {"postgres", "redis", "s3", "httpRequest", "emailSend", "readWriteFile", "rssFeedRead", "n8n", "telegram"}
FLOW_TYPES = {"if", "switch", "filter", "merge", "splitInBatches", "executeWorkflow", "wait", "code", "set"}
TYPE_LABELS = {
    "httpRequest": "HTTP Request", "respondToWebhook": "Respond to Webhook", "set": "Edit Fields", "if": "If",
    "splitInBatches": "Loop Over Items", "executeWorkflowTrigger": "Sub-workflow Trigger",
    "executeWorkflow": "Execute Workflow", "lmChatOllama": "Ollama Chat Model", "readWriteFile": "Read/Write File",
    "emailReadImap": "IMAP Trigger", "emailSend": "Send Email", "s3": "S3 / MinIO", "noOp": "No-op",
    "stopAndError": "Stop and Error", "n8n": "n8n API", "rssFeedRead": "RSS Read", "xml": "XML", "html": "HTML",
    "removeDuplicates": "Remove Duplicates", "vectorStoreQdrant": "Qdrant Vector Store",
    "embeddingsOllama": "Ollama Embeddings", "documentDefaultDataLoader": "Data Loader",
    "textSplitterRecursiveCharacterTextSplitter": "Text Splitter", "chainLlm": "LLM Chain",
    "chainRetrievalQa": "Retrieval QA", "chainSummarization": "Summarize", "outputParserStructured": "Output Parser",
    "informationExtractor": "Information Extractor", "textClassifier": "Text Classifier",
    "toolWorkflow": "Workflow Tool", "toolCode": "Code Tool", "memoryBufferWindow": "Window Memory",
    "retrieverVectorStore": "Vector Retriever", "agent": "AI Agent", "chatTrigger": "Chat Trigger",
}
LANE_LABELS = {
    "ai_languageModel": "model", "ai_memory": "memory", "ai_tool": "tool", "ai_outputParser": "parser",
    "ai_embedding": "embedding", "ai_document": "document", "ai_textSplitter": "splitter",
    "ai_vectorStore": "vector store", "ai_retriever": "retriever", "ai_reranker": "reranker",
}
OUTPUT_LABELS = {"if": ("true", "false"), "splitInBatches": ("done", "loop"), "filter": ("kept", "discarded")}
FONT_CANDIDATES = [
    "segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Helvetica.ttc",
    "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]
FONT_BOLD_CANDIDATES = [
    "segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def load_pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont, PngImagePlugin  # type: ignore
    except ImportError:
        print("render-preview.py needs Pillow: pip install pillow  (standard library cannot rasterise PNGs)",
              file=sys.stderr)
        return None
    return Image, ImageDraw, ImageFont, PngImagePlugin


# --- PNG marker (standard library; works without Pillow) ---------------------------------------------------------

def png_software_tag(path: Path) -> str | None:
    """Value of the tEXt chunk 'Software' or None (also None for non-PNG / unreadable files)."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos = 8
    while pos + 8 <= len(data):
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if ctype == b"tEXt" and b"\0" in body:
            key, _, value = body.partition(b"\0")
            if key == b"Software":
                return value.decode("latin-1", errors="replace")
        if ctype == b"IEND":
            break
        pos += 12 + length
    return None


def is_auto_preview(path: Path) -> bool:
    return png_software_tag(path) == SOFTWARE_TAG


# --- helpers -----------------------------------------------------------------------------------------------------

def humanize(type_: str) -> str:
    short = type_.rsplit(".", 1)[-1]
    if short in TYPE_LABELS:
        return TYPE_LABELS[short]
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", short).split()
    return " ".join(w if w.isupper() else w[:1].upper() + w[1:] for w in words)


def node_color(node: dict[str, Any]) -> tuple[int, int, int]:
    t = str(node.get("type", ""))
    short = t.rsplit(".", 1)[-1]
    if short == "stopAndError" or short == "errorTrigger":
        return COLOR_ERROR
    if t.startswith("@n8n/n8n-nodes-langchain."):
        return COLOR_AI
    if "trigger" in short.lower() or short == "webhook":
        return COLOR_TRIGGER
    if short in IO_TYPES:
        return COLOR_IO
    if short in FLOW_TYPES:
        return COLOR_FLOW
    return COLOR_DEFAULT


def bezier(p0, p1, p2, p3, steps: int = 28) -> list[tuple[float, float]]:
    pts = []
    for i in range(steps + 1):
        t = i / steps
        mt = 1 - t
        x = mt ** 3 * p0[0] + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t ** 3 * p3[0]
        y = mt ** 3 * p0[1] + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t ** 3 * p3[1]
        pts.append((x, y))
    return pts


def sticky_lines(content: str) -> list[tuple[str, bool]]:
    """Markdown-ish sticky text -> [(paragraph, is_heading)]."""
    out: list[tuple[str, bool]] = []
    for raw in content.replace("\r", "").split("\n"):
        line = raw.strip()
        if not line:
            out.append(("", False))
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            out.append((m.group(2).strip(), True))
        else:
            line = re.sub(r"[*_`]{1,2}([^*_`]+)[*_`]{1,2}", r"\1", line)
            out.append((line, False))
    return out


class Renderer:
    def __init__(self, pil, data: dict[str, Any]):
        self.Image, self.ImageDraw, self.ImageFont, self.PngImagePlugin = pil
        self.data = data
        nodes = [n for n in data.get("nodes") or [] if isinstance(n, dict)]
        self.stickies = [n for n in nodes if str(n.get("type", "")).endswith(".stickyNote")]
        self.nodes = [n for n in nodes if not str(n.get("type", "")).endswith(".stickyNote")]
        self.by_name = {str(n.get("name")): n for n in self.nodes}
        self.fonts: dict[tuple[str, int], Any] = {}

    # fonts ----------------------------------------------------------------------------------------------------
    def font(self, size: int, bold: bool = False):
        key = ("b" if bold else "r", size)
        if key in self.fonts:
            return self.fonts[key]
        f = None
        for cand in (FONT_BOLD_CANDIDATES if bold else FONT_CANDIDATES):
            try:
                f = self.ImageFont.truetype(cand, size)
                break
            except OSError:
                continue
        if f is None:
            try:
                f = self.ImageFont.load_default(size=size)
            except TypeError:
                f = self.ImageFont.load_default()
        self.fonts[key] = f
        return f

    def text_w(self, draw, text: str, font) -> float:
        try:
            return draw.textlength(text, font=font)
        except AttributeError:  # very old Pillow
            return draw.textsize(text, font=font)[0]

    def fit(self, draw, text: str, font, max_w: float) -> str:
        if self.text_w(draw, text, font) <= max_w:
            return text
        ell = "\u2026"
        while text and self.text_w(draw, text + ell, font) > max_w:
            text = text[:-1]
        return text + ell

    def wrap(self, draw, text: str, font, max_w: float) -> list[str]:
        words = text.split()
        lines: list[str] = []
        cur = ""
        for w in words:
            cand = (cur + " " + w).strip()
            if not cur or self.text_w(draw, cand, font) <= max_w:
                cur = cand
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return [self.fit(draw, ln, font, max_w) for ln in lines] or [""]

    # geometry -----------------------------------------------------------------------------------------------
    def _pos(self, node: dict[str, Any]) -> tuple[float, float]:
        p = node.get("position")
        if isinstance(p, list) and len(p) == 2:
            try:
                return float(p[0]), float(p[1])
            except (TypeError, ValueError):
                pass
        return 0.0, 0.0

    def _rect(self, node: dict[str, Any]) -> tuple[float, float, float, float]:
        x, y = self._pos(node)
        if node in self.stickies:
            params = node.get("parameters") or {}
            w = float(params.get("width") or 240)
            h = float(params.get("height") or 160)
            return x, y, x + w, y + h
        return x, y, x + NODE_W, y + NODE_H

    def bbox(self) -> tuple[float, float, float, float]:
        rects = [self._rect(n) for n in self.nodes + self.stickies] or [(0.0, 0.0, NODE_W, NODE_H)]
        return (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects))

    # render ---------------------------------------------------------------------------------------------------
    def render(self):
        x0, y0, x1, y1 = self.bbox()
        content_w = (x1 - x0) + 2 * MARGIN
        content_h = (y1 - y0) + 2 * MARGIN
        scale = min(max(MIN_WIDTH / content_w, MIN_SCALE), MAX_SCALE)
        canvas_w = max(MIN_WIDTH, int(round(content_w * scale)))
        canvas_h = max(360, int(round(content_h * scale)))
        offset_x = (canvas_w - content_w * scale) / 2 + (MARGIN - x0) * scale
        offset_y = TITLE_H + (MARGIN - y0) * scale
        self.scale = scale

        def tx(x: float) -> float:
            return offset_x + x * scale

        def ty(y: float) -> float:
            return offset_y + y * scale

        img = self.Image.new("RGB", (canvas_w, TITLE_H + canvas_h + FOOTER_H), BG)
        draw = self.ImageDraw.Draw(img)
        self._grid(draw, canvas_w, canvas_h, scale)
        for s in self.stickies:
            self._sticky(draw, s, tx, ty)
        self._connections(draw, tx, ty)
        for n in self.nodes:
            self._node(draw, n, tx, ty)
        self._title(draw, canvas_w)
        self._footer(draw, canvas_w, TITLE_H + canvas_h)
        return img

    def _grid(self, draw, w: int, h: int, scale: float) -> None:
        step = max(12, int(20 * scale))
        r = 1
        for gx in range(step, w, step):
            for gy in range(TITLE_H + step, TITLE_H + h, step):
                draw.ellipse((gx - r, gy - r, gx + r, gy + r), fill=GRID)

    def _title(self, draw, w: int) -> None:
        draw.rectangle((0, 0, w, TITLE_H), fill=TITLE_BG)
        name = str(self.data.get("name") or "workflow")
        wid = str(self.data.get("id") or "")
        f_title = self.font(24, bold=True)
        f_meta = self.font(14)
        meta = f"id {wid}  \u00b7  {len(self.nodes)} nodes"
        if self.stickies:
            meta += f"  \u00b7  {len(self.stickies)} note{'s' if len(self.stickies) != 1 else ''}"
        meta_w = self.text_w(draw, meta, f_meta)
        draw.text((24, (TITLE_H - 28) / 2), self.fit(draw, name, f_title, w - meta_w - 72), fill=TITLE_FG, font=f_title)
        draw.text((w - 24 - meta_w, (TITLE_H - 16) / 2), meta, fill=TITLE_MUTED, font=f_meta)

    def _footer(self, draw, w: int, top: int) -> None:
        draw.rectangle((0, top, w, top + FOOTER_H), fill=(236, 238, 241))
        draw.line((0, top, w, top), fill=GRID, width=1)
        f = self.font(13)
        text = "auto-rendered preview - open in n8n for the live canvas"
        draw.text(((w - self.text_w(draw, text, f)) / 2, top + (FOOTER_H - 15) / 2), text, fill=FOOTER_FG, font=f)

    def _sticky(self, draw, node: dict[str, Any], tx, ty) -> None:
        x0, y0, x1, y1 = self._rect(node)
        params = node.get("parameters") or {}
        try:
            color = int(params.get("color") or 1)
        except (TypeError, ValueError):
            color = 1
        fill, border = STICKY_COLORS.get(color, STICKY_COLORS[1])
        box = (tx(x0), ty(y0), tx(x1), ty(y1))
        draw.rounded_rectangle(box, radius=8, fill=fill, outline=border, width=2)
        pad = 12
        max_w = (box[2] - box[0]) - 2 * pad
        y = box[1] + pad
        f_head = self.font(17, bold=True)
        f_body = self.font(14)
        for para, is_head in sticky_lines(str(params.get("content") or "")):
            font = f_head if is_head else f_body
            line_h = (20 if is_head else 17)
            if not para:
                y += line_h * 0.5
                continue
            for ln in self.wrap(draw, para, font, max_w):
                if y + line_h > box[3] - pad:
                    draw.text((box[0] + pad, y - line_h * 0.9), "\u2026", fill=NODE_SUB, font=f_body)
                    return
                draw.text((box[0] + pad, y), ln, fill=NODE_TEXT, font=font)
                y += line_h

    def _node(self, draw, node: dict[str, Any], tx, ty) -> None:
        x0, y0, x1, y1 = self._rect(node)
        box = (tx(x0), ty(y0), tx(x1), ty(y1))
        color = node_color(node)
        shadow = (box[0] + 2, box[1] + 3, box[2] + 2, box[3] + 3)
        draw.rounded_rectangle(shadow, radius=10, fill=(226, 229, 234))
        draw.rounded_rectangle(box, radius=10, fill=NODE_FILL, outline=color, width=2)
        draw.rounded_rectangle((box[0], box[1], box[0] + 7, box[3]), radius=4, fill=color)
        pad = 14
        max_w = (box[2] - box[0]) - pad - 8
        name = str(node.get("name") or "?")
        f_name = self.font(15, bold=True)
        f_type = self.font(12)
        h = box[3] - box[1]
        draw.text((box[0] + pad, box[1] + h * 0.22), self.fit(draw, name, f_name, max_w), fill=NODE_TEXT, font=f_name)
        label = humanize(str(node.get("type", "")))
        if node.get("disabled"):
            label += " (disabled)"
        draw.text((box[0] + pad, box[1] + h * 0.58), self.fit(draw, label, f_type, max_w), fill=NODE_SUB, font=f_type)

    def _connections(self, draw, tx, ty) -> None:
        conns = self.data.get("connections") or {}
        if not isinstance(conns, dict):
            return
        lw = max(2, int(round(2 * self.scale)))
        f_lane = self.font(11)
        # how many main lanes / inputs each node has (for anchor spacing)
        in_count: dict[str, int] = {}
        ai_children: dict[str, list[tuple[str, str]]] = {}
        for src, kinds in conns.items():
            if not isinstance(kinds, dict):
                continue
            for kind, lanes in kinds.items():
                for lane in lanes or []:
                    for edge in lane or []:
                        if not isinstance(edge, dict):
                            continue
                        dst = str(edge.get("node"))
                        if kind == "main":
                            in_count[dst] = max(in_count.get(dst, 0), int(edge.get("index") or 0) + 1)
                        else:
                            ai_children.setdefault(dst, []).append((str(kind), str(src)))
        for parent, subs in ai_children.items():
            subs.sort(key=lambda ks: self._pos(self.by_name[ks[1]])[0] if ks[1] in self.by_name else 0)
        for src, kinds in conns.items():
            if src not in self.by_name or not isinstance(kinds, dict):
                continue
            sx0, sy0, sx1, sy1 = self._rect(self.by_name[src])
            for kind, lanes in kinds.items():
                lanes = lanes or []
                if kind == "main":
                    n_out = max(1, len(lanes))
                    short = str(self.by_name[src].get("type", "")).rsplit(".", 1)[-1]
                    labels = OUTPUT_LABELS.get(short, ())
                    for i, lane in enumerate(lanes):
                        ay = sy0 + (sy1 - sy0) * (i + 1) / (n_out + 1)
                        start = (tx(sx1), ty(ay))
                        if n_out > 1:
                            lab = labels[i] if i < len(labels) else f"out {i}"
                            draw.text((start[0] + 6, start[1] - 14), lab, fill=LANE_TEXT, font=f_lane)
                        for edge in lane or []:
                            if not isinstance(edge, dict) or str(edge.get("node")) not in self.by_name:
                                continue
                            dst = str(edge.get("node"))
                            dx0, dy0, dx1, dy1 = self._rect(self.by_name[dst])
                            n_in = max(1, in_count.get(dst, 1))
                            by = dy0 + (dy1 - dy0) * (int(edge.get("index") or 0) + 1) / (n_in + 1)
                            end = (tx(dx0), ty(by))
                            self._main_edge(draw, start, end, lw)
                else:
                    # sub-node (src) -> parent (dst) via an ai_* lane: top of sub-node to bottom of parent
                    for lane in lanes:
                        for edge in lane or []:
                            if not isinstance(edge, dict) or str(edge.get("node")) not in self.by_name:
                                continue
                            dst = str(edge.get("node"))
                            px0, py0, px1, py1 = self._rect(self.by_name[dst])
                            subs = ai_children.get(dst, [])
                            try:
                                j = subs.index((str(kind), src))
                            except ValueError:
                                j = 0
                            ax = px0 + (px1 - px0) * (j + 1) / (len(subs) + 1)
                            start = (tx((sx0 + sx1) / 2), ty(sy0))
                            end = (tx(ax), ty(py1))
                            self._ai_edge(draw, start, end, lw)
                            lab = LANE_LABELS.get(str(kind), str(kind).replace("ai_", ""))
                            draw.text((start[0] + 6, start[1] - 16), lab, fill=AI_LINE, font=f_lane)

    def _main_edge(self, draw, start, end, lw: int) -> None:
        dx = end[0] - start[0]
        if dx >= 0:
            c = max(40 * self.scale, dx * 0.5)
            pts = bezier(start, (start[0] + c, start[1]), (end[0] - c, end[1]), end)
        else:  # backward edge (loop): swing below
            c = max(120 * self.scale, abs(dx) * 0.6)
            pts = bezier(start, (start[0] + c, start[1] + c * 0.8), (end[0] - c, end[1] + c * 0.8), end)
        draw.line(pts, fill=MAIN_LINE, width=lw, joint="curve")
        a = 6 * max(1.0, self.scale)
        draw.polygon([(end[0], end[1]), (end[0] - a * 1.6, end[1] - a), (end[0] - a * 1.6, end[1] + a)], fill=MAIN_LINE)

    def _ai_edge(self, draw, start, end, lw: int) -> None:
        c = max(30 * self.scale, abs(end[1] - start[1]) * 0.5)
        pts = bezier(start, (start[0], start[1] - c), (end[0], end[1] + c), end)
        on = True
        for i in range(len(pts) - 1):
            if on:
                draw.line([pts[i], pts[i + 1]], fill=AI_LINE, width=lw)
            on = not on
        r = 4 * max(1.0, self.scale)
        draw.ellipse((end[0] - r, end[1] - r, end[0] + r, end[1] + r), fill=AI_LINE)

    # output ---------------------------------------------------------------------------------------------------
    def save(self, out: Path) -> tuple[int, int]:
        img = self.render()
        info = self.PngImagePlugin.PngInfo()
        info.add_text("Software", SOFTWARE_TAG)
        info.add_text("Comment", f"rendered from workflow.json by scripts/render-preview.py (id={self.data.get('id', '')})")
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, format="PNG", optimize=True, pnginfo=info)
        return img.size


# --- driver -----------------------------------------------------------------------------------------------------

def render_folder(folder: Path, root: Path, *, force: bool = False, out: Path | None = None,
                  pil=None) -> tuple[bool, str]:
    """Returns (ok, message). Skipping a real capture counts as ok."""
    where = validate.rel(folder, root)
    wf = folder / "workflow.json"
    if not wf.exists():
        return False, f"{where}: no workflow.json to render"
    try:
        data = json.loads(validate.read_text(wf))
    except Exception as exc:  # noqa: BLE001
        return False, f"{where}: workflow.json is not valid JSON ({exc})"
    target = out or (folder / "assets" / "screenshot.png")
    if target.exists() and not force and not is_auto_preview(target):
        return True, f"{where}: kept existing screenshot (looks like a real capture; use --force to overwrite)"
    pil = pil or load_pillow()
    if pil is None:
        return False, f"{where}: Pillow is not installed"
    size = Renderer(pil, data).save(target)
    return True, f"{where}: wrote {validate.rel(target, root)} ({size[0]}x{size[1]})"


def resolve_targets(root: Path, args: list[str], all_: bool) -> tuple[list[Path], list[str]]:
    errors: list[str] = []
    if all_:
        folders, errs = validate.discover_folders(root)
        return [f for f in folders if (f / "workflow.json").exists()], errs
    found: list[Path] = []
    for raw in args:
        p = Path(raw)
        cand = p if p.exists() else root / raw
        if not cand.exists() and validate.ID_RE.match(raw):
            base = root / ("patterns" if raw.startswith("P") else "workflows")
            matches = sorted(base.glob(f"{raw}-*")) if base.is_dir() else []
            if matches:
                cand = matches[0]
        if not cand.exists():
            errors.append(f"{raw}: folder not found")
            continue
        found.append(cand.resolve() if cand.is_dir() else cand.resolve().parent)
    return found, errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folders", nargs="*", help="workflow/pattern folders or catalog ids (T01, P03 ...)")
    ap.add_argument("--all", action="store_true", help="render every folder that has a workflow.json")
    ap.add_argument("--force", action="store_true", help="overwrite screenshots that look like real captures")
    ap.add_argument("--out", default=None, help="write the PNG here instead of <folder>/assets/screenshot.png")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="repo root (default: parent of scripts/)")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    if not args.folders and not args.all:
        ap.print_usage()
        print("error: give at least one folder / id, or --all", file=sys.stderr)
        return 2
    root = Path(args.root).resolve()
    targets, errors = resolve_targets(root, args.folders, args.all)
    for e in errors:
        print(f"error: {e}", file=sys.stderr)
    if args.out and len(targets) > 1:
        print("error: --out only works with a single folder", file=sys.stderr)
        return 2
    pil = load_pillow()
    if pil is None:
        return 1
    failed = bool(errors)
    for folder in targets:
        ok, msg = render_folder(folder, root, force=args.force, out=Path(args.out) if args.out else None, pil=pil)
        print(msg if ok else f"error: {msg}", file=sys.stdout if ok else sys.stderr)
        failed = failed or not ok
    if not targets and not errors:
        print("nothing to render (no folder has a workflow.json yet)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
