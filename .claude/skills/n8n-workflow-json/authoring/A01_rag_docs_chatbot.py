#!/usr/bin/env python
"""A01 - RAG chatbot over the repo docs (Qdrant + Ollama, everything local).

One workflow, two disconnected paths that share one Qdrant collection (`lab_docs`):

INGEST (manual trigger - an operator action)
    Read docs/**/*.md + patterns/*/README.md -> extract text -> SHA-256 per file -> scroll the collection for
    the hashes already indexed -> plan (added / changed / unchanged / removed) -> delete the chunks of changed
    and removed files -> chunk + embed only what changed (nomic-embed-text, batched) -> upsert with
    deterministic point ids -> count -> one P08 log row. A re-run with nothing changed embeds nothing.

QUERY (chat trigger - the demo)
    Question -> embed -> Qdrant similarity search (top 6) -> keep chunks above a score floor -> numbered
    context -> llama3.2:3b with an "answer only from these passages, else say NOT_IN_DOCS" contract -> the
    reply carries the passages it used with their scores. No context above the floor, an unusable model answer
    or an unreachable index all produce an honest "I don't know" instead of an invention.

Why Qdrant is driven over its REST API instead of the Qdrant Vector Store node
-----------------------------------------------------------------------------
`@n8n/n8n-nodes-langchain.vectorStoreQdrant` uses `@qdrant/js-client-rest`, which builds an
`undici@6` Agent and hands it to Node 26's built-in `fetch` (undici 7 handler API). Every call dies with
`TypeError: fetch failed` / `InvalidArgumentError: invalid onError method`, in the main process and in the
CLI alike - verified on this stack, see the README "Notes & trade-offs". The HTTP Request node (axios) talks
to the same Qdrant happily, so the vector store is built from HTTP + Code nodes: same collection, same payload
shape (`content` + `metadata`) the LangChain node would write, so the node can be swapped back in later.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, catalog_id, chat_trigger, code, cond_bool, cond_num, crypto_hash,  # noqa: E402
                         execute_workflow, extract, http, if_, llm_chain, manual_trigger, merge, read_file,
                         respond, set_fields)

# --------------------------------------------------------------------------------------------------
# constants that appear in more than one place
# --------------------------------------------------------------------------------------------------
COLLECTION = "lab_docs"
QDRANT = f"http://qdrant:6333/collections/{COLLECTION}"
OLLAMA_EMBED = "http://ollama:11434/api/embed"
CHAT_MODEL = "llama3.2:3b"
EMBED_MODEL = "nomic-embed-text:latest"
DOCS_GLOB = "/home/node/.n8n-files/docs/**/*.md"
PATTERNS_GLOB = "/home/node/.n8n-files/patterns/*/README.md"

SYSTEM = (
    "You are the documentation assistant of the Automation Lab repository. You answer ONLY from the numbered "
    "context passages in the user message.\n"
    "Rules:\n"
    "1. If the passages do not contain the answer, reply with exactly NOT_IN_DOCS and nothing else.\n"
    "2. Never add knowledge from outside the passages. Never invent file names, workflow ids, node names or "
    "numbers - if a detail is not in a passage, it does not exist.\n"
    "3. Cite the passage you used in square brackets after each statement, like this: ... a Redis INCR guard [2].\n"
    "4. Answer in at most 120 words of plain English. No headings, no preamble, no apology."
)

# --------------------------------------------------------------------------------------------------
# ingest path - Code nodes
# --------------------------------------------------------------------------------------------------
PREPARE_DOCS_JS = r"""
// One item per markdown file. The Read node puts the path in binary.data.{directory,fileName} and the Extract
// node was told to keep both the binary and the json, so the text and its path travel on the same item.
const ROOT = '/home/node/.n8n-files/';
const out = [];
for (const item of $input.all()) {
  const bin = (item.binary && item.binary.data) || {};
  const dir = String(bin.directory || '').replace(/\/+$/, '');
  const file = String(bin.fileName || '');
  if (!file) continue;                                   // alwaysOutputData placeholder from an empty glob
  let source = dir ? dir + '/' + file : file;
  if (source.startsWith(ROOT)) source = source.slice(ROOT.length);   // -> docs/PRD.md, patterns/P01-.../README.md
  let text = String(item.json.content ?? '').replace(/\r\n/g, '\n');
  // Front matter is metadata, not prose: take its title and drop the block.
  let fmTitle = '';
  const fm = text.match(/^---\n([\s\S]*?)\n---\n/);
  if (fm) {
    const t = fm[1].match(/^title:\s*(.+)$/m);
    if (t) fmTitle = t[1].trim().replace(/^["']|["']$/g, '');
    text = text.slice(fm[0].length);
  }
  const h1 = text.match(/^#\s+(.+)$/m);
  const title = String(fmTitle || (h1 ? h1[1] : file)).trim().slice(0, 120);
  text = text.replace(/\n{3,}/g, '\n\n').trim();
  if (!text) continue;
  out.push({ json: { source, title, text, chars: text.length } });
}
out.sort((a, b) => a.json.source.localeCompare(b.json.source));
if (out.length === 0) {
  throw new Error('corpus is empty: no readable markdown under /home/node/.n8n-files/docs or .../patterns');
}
return out;
"""

PLAN_JS = r"""
// Compare the corpus on disk with what the collection already holds, per file, by content hash.
// This is what makes a re-run cheap: only new and changed files are embedded again.
const cfg = $('Index settings').first().json;
const force = cfg.force_rebuild === true || cfg.force_rebuild === 'true';
const res = $input.first().json || {};
const status = Number(res.statusCode || 0);
const points = (((res.body || {}).result) || {}).points || [];

// source -> { hash, chunks } as currently indexed
const indexed = new Map();
for (const p of points) {
  const md = ((p || {}).payload || {}).metadata || {};
  const src = md.source;
  if (!src) continue;
  const seen = indexed.get(src) || { hash: md.content_hash || null, chunks: 0 };
  seen.chunks += 1;
  if (!seen.hash) seen.hash = md.content_hash || null;
  indexed.set(src, seen);
}

const docs = $('Hash content').all().map(i => i.json);
const added = [], changed = [], unchanged = [];
for (const d of docs) {
  const prev = indexed.get(d.source);
  if (!prev) added.push(d.source);
  else if (force || prev.hash !== d.content_hash) changed.push(d.source);
  else unchanged.push(d.source);
}
const onDisk = new Set(docs.map(d => d.source));
const removed = [...indexed.keys()].filter(s => !onDisk.has(s)).sort();
// The chunks of a changed file go before the new ones are written, or the collection holds both versions.
const delete_sources = [...new Set([...changed, ...removed])].sort();
const embed = new Set([...added, ...changed]);
const files_to_embed = docs.filter(d => embed.has(d.source));

const notes = [
  'corpus ' + docs.length + ' file(s)',
  status === 200 ? ('index ' + indexed.size + ' file(s) / ' + points.length + ' chunk(s)')
                 : ('index not readable (HTTP ' + status + ') - treated as empty'),
  added.length + ' new, ' + changed.length + ' changed, ' + unchanged.length + ' unchanged, ' +
    removed.length + ' removed',
  force ? 'force_rebuild: every file re-embedded' : null,
].filter(Boolean).join('; ');

return [{ json: {
  collection_readable: status === 200,
  force_rebuild: force,
  indexed_points: points.length,
  indexed_sources: indexed.size,
  corpus_files: docs.length,
  corpus_chars: docs.reduce((a, d) => a + Number(d.chars || 0), 0),
  added, changed, unchanged, removed, delete_sources,
  delete_count: delete_sources.length,
  embed_count: files_to_embed.length,
  embed_chars: files_to_embed.reduce((a, d) => a + Number(d.chars || 0), 0),
  files_to_embed,
  plan_notes: notes,
} }];
"""

CHUNK_JS = r"""
// Recursive character splitter (the same idea as LangChain's): break on the coarsest separator that fits,
// then pack the pieces up to chunk_size with chunk_overlap characters carried over from the previous chunk.
// One output item per embedding batch, so the HTTP node below makes one Ollama call per batch.
const cfg = $('Index settings').first().json;
const SIZE = Number(cfg.chunk_size) || 900;
const OVERLAP = Number(cfg.chunk_overlap) || 150;
const BATCH = Number(cfg.embed_batch_size) || 16;
const indexed_at = new Date().toISOString();

function atomize(text) {
  const out = [];
  const push = (s) => { if (s && s.trim()) out.push(s.trim()); };
  for (const para of text.split(/\n{2,}/)) {
    if (para.length <= SIZE) { push(para); continue; }
    for (const line of para.split('\n')) {
      if (line.length <= SIZE) { push(line); continue; }
      for (const sentence of line.split(/(?<=[.!?])\s+/)) {
        if (sentence.length <= SIZE) { push(sentence); continue; }
        for (let i = 0; i < sentence.length; i += SIZE) push(sentence.slice(i, i + SIZE));
      }
    }
  }
  return out;
}

function chunk(text) {
  const atoms = atomize(text);
  const chunks = [];
  let buf = [], len = 0;
  for (const atom of atoms) {
    if (len + atom.length + 1 > SIZE && buf.length) {
      chunks.push(buf.join('\n'));
      const keep = [];
      let k = 0;
      for (let i = buf.length - 1; i >= 0; i--) {
        if (k + buf[i].length + 1 > OVERLAP) break;
        keep.unshift(buf[i]);
        k += buf[i].length + 1;
      }
      buf = keep; len = k;
    }
    buf.push(atom); len += atom.length + 1;
  }
  if (buf.length) chunks.push(buf.join('\n'));
  return chunks.filter(c => c.trim().length > 0);
}

const files = $('Plan the reindex').first().json.files_to_embed || [];
const all = [];
for (const d of files) {
  const parts = chunk(String(d.text || ''));
  parts.forEach((text, i) => all.push({
    source: d.source, title: d.title, content_hash: d.content_hash,
    chunk_index: i, chunk_count: parts.length, chars: text.length, text, indexed_at,
  }));
}
if (all.length === 0) throw new Error('nothing to embed after chunking - check chunk_size in Index settings');

const batches = [];
for (let i = 0; i < all.length; i += BATCH) {
  const slice = all.slice(i, i + BATCH);
  batches.push({ json: {
    batch_index: batches.length,
    batch_count: Math.ceil(all.length / BATCH),
    total_chunks: all.length,
    total_files: files.length,
    inputs: slice.map(c => c.text),
    chunks: slice,
  } });
}
return batches;
"""

POINTS_JS = r"""
// Zip each batch with its embedding response (same order in, same order out) and build Qdrant points.
// The point id is derived from the file's content hash + the chunk index, so re-upserting identical content
// overwrites the same points instead of adding copies - the last line of defence against duplicate vectors.
const batches = $('Chunk and batch').all().map(i => i.json);
const responses = $input.all().map(i => i.json);
const pointId = (hash, idx) => [
  hash.slice(0, 8), hash.slice(8, 12), hash.slice(12, 16), hash.slice(16, 20),
  Number(idx).toString(16).padStart(12, '0'),
].join('-');

const out = [];
for (let b = 0; b < batches.length; b++) {
  const chunks = batches[b].chunks || [];
  const vectors = ((responses[b] || {}).embeddings) || [];
  if (vectors.length !== chunks.length) {
    throw new Error('batch ' + b + ': got ' + vectors.length + ' embeddings for ' + chunks.length + ' chunks');
  }
  const points = chunks.map((c, i) => ({
    id: pointId(String(c.content_hash), c.chunk_index),
    vector: vectors[i],
    payload: {
      content: c.text,
      metadata: {
        source: c.source, title: c.title, content_hash: c.content_hash,
        chunk_index: c.chunk_index, chunk_count: c.chunk_count, indexed_at: c.indexed_at,
      },
    },
  }));
  out.push({ json: { batch_index: b, point_count: points.length, dimensions: (vectors[0] || []).length, points } });
}
return out;
"""

INDEX_SUMMARY_JS = r"""
// Runs on both lanes: after an upsert, and after "nothing changed". Reads the truth back from Qdrant.
const cfg = $('Index settings').first().json;
const plan = $('Plan the reindex').first().json;
const res = $input.first().json || {};
const info = ((res.body || {}).result) || {};
const points = Number(info.points_count ?? 0);
const vectors = (((info.config || {}).params || {}).vectors) || {};
const duration_ms = Date.now() - new Date(cfg.started_at).getTime();
const touched = plan.embed_count > 0 || plan.delete_count > 0;

let chunks_written = 0;
try { chunks_written = $('Build Qdrant points').all().reduce((a, i) => a + Number(i.json.point_count || 0), 0); }
catch (e) { chunks_written = 0; }                     // the "nothing changed" lane never ran that node

const notes = [
  'corpus ' + plan.corpus_files + ' file(s) / ' + plan.corpus_chars + ' chars -> ' + points +
    ' chunk(s) in ' + cfg.collection,
  'embedded ' + plan.embed_count + ' file(s) (' + plan.added.length + ' new, ' + plan.changed.length +
    ' changed) = ' + chunks_written + ' chunk(s), skipped ' + plan.unchanged.length + ' unchanged',
  plan.delete_count ? ('dropped the chunks of ' + plan.delete_count + ' file(s)') : null,
  'took ' + Math.round(duration_ms / 1000) + ' s',
].filter(Boolean).join('; ');

return [{ json: {
  collection: cfg.collection,
  points_count: points,
  vector_size: vectors.size ?? null,
  distance: vectors.distance ?? null,
  corpus_files: plan.corpus_files,
  corpus_chars: plan.corpus_chars,
  embedded_files: plan.embed_count,
  embedded_chars: plan.embed_chars,
  chunks_written,
  deleted_sources: plan.delete_count,
  unchanged_files: plan.unchanged.length,
  added: plan.added,
  changed: plan.changed,
  removed: plan.removed,
  force_rebuild: plan.force_rebuild,
  duration_ms,
  status: touched ? 'success' : 'info',
  notes,
} }];
"""

# --------------------------------------------------------------------------------------------------
# query path - Code nodes
# --------------------------------------------------------------------------------------------------
PREPARE_QUESTION_JS = r"""
// --- retrieval settings (the only knobs on the query path) -------------------------------------
const TOP_K = 6;          // chunks pulled from Qdrant
const MIN_SCORE = 0.45;   // cosine similarity floor; below it we say "not in the docs" without asking the model
const MAX_SOURCES = 4;    // passages shown to the model (and cited back to the user)
// -----------------------------------------------------------------------------------------------
const j = $input.first().json || {};
const question = String(j.chatInput ?? '').replace(/\s+/g, ' ').trim();
return [{ json: {
  question: question.slice(0, 500),
  asked: question.length > 0,
  session_id: j.sessionId ?? null,
  top_k: TOP_K,
  min_score: MIN_SCORE,
  max_sources: MAX_SOURCES,
  asked_at: new Date().toISOString(),
} }];
"""

BUILD_CONTEXT_JS = r"""
// Turn the search response into a numbered context block, and decide whether there is anything worth asking.
const q = $('Prepare question').first().json;
const res = $input.first().json || {};
const status = Number(res.statusCode || 0);
const body = res.body || {};
const hits = [];
if (status === 200) {
  for (const h of (body.result || [])) {
    const md = ((h || {}).payload || {}).metadata || {};
    hits.push({
      score: Number(h.score ?? 0),
      source: String(md.source || 'unknown'),
      title: String(md.title || ''),
      text: String((h.payload || {}).content ?? '').trim(),
    });
  }
}
hits.sort((a, b) => b.score - a.score);
const kept = hits.filter(h => h.score >= q.min_score).slice(0, q.max_sources);
const sources = kept.map((h, i) => ({
  n: i + 1, source: h.source, title: h.title, score: Math.round(h.score * 1000) / 1000,
  snippet: h.text.replace(/\s+/g, ' ').slice(0, 240),
}));
const context = kept.map((h, i) =>
  '[' + (i + 1) + '] ' + h.source + (h.title ? ' - ' + h.title : '') + '\n' + h.text).join('\n\n---\n\n');

// 404 = the collection does not exist yet (nobody has run the ingest path); anything else non-200 is a fault.
const reason = !q.asked ? 'not_asked'
  : status === 404 ? 'empty_index'
  : status !== 200 ? 'index_error'
  : hits.length === 0 ? 'empty_index'
  : kept.length === 0 ? 'below_threshold'
  : 'ok';

return [{ json: {
  question: q.question,
  asked: q.asked,
  has_context: q.asked && kept.length > 0 && reason === 'ok',
  reason,
  detail: status === 200 ? null : ('Qdrant answered HTTP ' + status),
  retrieved: hits.length,
  top_score: hits.length ? Math.round(hits[0].score * 1000) / 1000 : null,
  min_score: q.min_score,
  sources,
  near_misses: hits.slice(0, 3).map(h => ({ source: h.source, score: Math.round(h.score * 1000) / 1000 })),
  context,
  prompt: 'CONTEXT PASSAGES\n\n' + context + '\n\nQUESTION\n' + q.question,
} }];
"""

NO_CONTEXT_JS = r"""
// Nothing above the score floor (or an empty index, or an empty message): the model is never called.
const c = $input.first().json;
const detail = c.reason === 'below_threshold'
  ? ('best score ' + c.top_score + ' < min_score ' + c.min_score)
  : c.reason === 'empty_index' ? 'the collection returned no chunks'
  : c.reason === 'not_asked' ? 'empty message'
  : (c.detail || 'the search did not answer');
return [{ json: {
  question: c.question,
  reason: c.reason,
  detail,
  sources: [],
  near_misses: c.near_misses || [],
  retrieved: c.retrieved,
  top_score: c.top_score,
} }];
"""

MODEL_UNAVAILABLE_JS = r"""
// Error output of the LLM chain: Ollama unreachable, out of memory, or a reply the chain could not read.
// One question per call, so this lane is trustworthy (see patterns/P05-sub-workflows/README.md).
const c = $('Build grounded context').first().json;
const e = $input.first().json || {};
const err = e.error ?? e;
return [{ json: {
  question: c.question,
  reason: 'model_error',
  detail: String(err?.message ?? err?.description ?? err ?? 'unknown error').replace(/\s+/g, ' ').slice(0, 300),
  sources: c.sources || [],
  near_misses: c.near_misses || [],
  retrieved: c.retrieved,
  top_score: c.top_score,
} }];
"""

RETRIEVAL_FAILED_JS = r"""
// Connection-level failure of the embedding call or of the Qdrant search. The chat still answers.
const q = $('Prepare question').first().json;
const e = $input.first().json || {};
const err = e.error ?? e;
return [{ json: {
  question: q.question,
  reason: 'index_error',
  detail: String(err?.message ?? err?.description ?? err ?? 'unknown error').replace(/\s+/g, ' ').slice(0, 300),
  sources: [],
  near_misses: [],
  retrieved: 0,
  top_score: null,
} }];
"""

REPLY_JS = r"""
// One shape for every path: the model's answer, or an honest refusal. Nothing here invents content.
const j = $input.first().json || {};
const fromModel = j.reason === undefined;          // the chain outputs {text}; every fallback sets a reason

let question, sources, near_misses, retrieved, top_score;
let detail = j.detail ?? null;
let answer = null;
if (fromModel) {
  const c = $('Build grounded context').first().json;
  question = c.question; sources = c.sources || []; near_misses = c.near_misses || [];
  retrieved = c.retrieved; top_score = c.top_score;
  answer = String(j.text ?? '').trim();
} else {
  question = j.question; sources = j.sources || []; near_misses = j.near_misses || [];
  retrieved = j.retrieved || 0; top_score = j.top_score ?? null;
}

const refused = !answer || answer.toUpperCase().includes('NOT_IN_DOCS');
const reason = fromModel ? (refused ? 'not_in_docs' : 'ok') : j.reason;
let grounded = false;
let sources_used = [];
let output;

if (fromModel && !refused) {
  // Show the passages the answer actually cites; if it cited none, show everything it was given.
  const cited = new Set((answer.match(/\[(\d+)\]/g) || [])
    .map(m => Number(m.slice(1, -1)))
    .filter(n => n >= 1 && n <= sources.length));
  sources_used = cited.size ? sources.filter(s => cited.has(s.n)) : sources;
  grounded = true;
  output = answer + '\n\nSources:\n' +
    sources_used.map(s => '[' + s.n + '] ' + s.source + (s.title ? ' - ' + s.title : '') +
      '  (similarity ' + s.score.toFixed(3) + ')').join('\n');
} else {
  const MESSAGES = {
    not_in_docs: 'I could not answer that from the indexed Automation Lab docs. The passages I retrieved do not ' +
                 'contain it, and I will not guess.',
    below_threshold: 'Nothing in the indexed docs is close enough to that question, so I have no grounded answer.',
    empty_index: 'The docs index is empty. Run the ingest path of A01 once (Reindex the docs) and ask again.',
    not_asked: 'Ask me something about the Automation Lab docs - the patterns, the stack, the PRD or the ADRs.',
    model_error: 'The local model did not return an answer, so there is nothing I can ground. Try again in a moment.',
    index_error: 'I could not reach the docs index, so I have no passages to answer from.',
  };
  output = MESSAGES[reason] || 'I have no grounded answer for that.';
  if (sources.length) {
    output += '\n\nPassages I did retrieve:\n' +
      sources.map(s => '[' + s.n + '] ' + s.source + '  (similarity ' + s.score.toFixed(3) + ')').join('\n');
  } else if (near_misses.length) {
    output += '\n\nClosest passages, below the ' + $('Prepare question').first().json.min_score + ' floor:\n' +
      near_misses.map(h => '- ' + h.source + '  (similarity ' + h.score.toFixed(3) + ')').join('\n');
  }
  if (detail) output += '\n\n(' + detail + ')';
}

return [{ json: {
  output,
  grounded,
  reason,
  question,
  sources_used,
  sources_retrieved: sources.length,
  chunks_searched: retrieved,
  top_score,
  detail,
  model: 'llama3.2:3b',
  collection: 'lab_docs',
  answered_at: new Date().toISOString(),
} }];
"""


def build() -> Workflow:
    wf = Workflow("A01", "rag-docs-chatbot", "RAG Chatbot over the Repo Docs", tags=["AI"],
                  error_workflow=catalog_id("P01"), execution_timeout=1800,
                  description="Indexes the repository's own markdown into a local Qdrant collection with "
                              "nomic-embed-text (incrementally, by content hash) and answers questions about it "
                              "with llama3.2:3b, quoting the chunks it used and refusing when the docs do not "
                              "contain the answer.")

    # ================================================================================================
    # INGEST PATH - operator action, re-runnable, embeds only what changed
    # ================================================================================================
    reindex = manual_trigger(wf, "Reindex the docs (manual)").at(-340, 0)
    cfg = set_fields(wf, "Index settings", {
        "started_at": "={{ $now.toISO() }}",
        "collection": COLLECTION,
        "force_rebuild": False,
        "chunk_size": 900,
        "chunk_overlap": 150,
        "embed_batch_size": 16,
        "vector_size": 768,
    }).at(-80, 0)
    cfg.note("Every knob of the index in one place. force_rebuild re-embeds everything (after a model or "
             "chunk-size change); vector_size is the nomic-embed-text dimension.")

    read_docs = read_file(wf, "Read docs/**/*.md", DOCS_GLOB).at(180, -140).always_output()
    read_patterns = read_file(wf, "Read patterns/*/README.md", PATTERNS_GLOB).at(180, 120).always_output()
    corpus = merge(wf, "Corpus files", "append").at(440, 0)
    text = extract(wf, "Extract markdown text", "text", keepSource="both").at(700, 0)
    text.parameters["destinationKey"] = "content"
    prepare = code(wf, "Prepare documents", PREPARE_DOCS_JS).at(960, 0)
    prepare.note("Repo-relative source path + title per file; front matter dropped.")
    hashed = crypto_hash(wf, "Hash content", "={{ $json.text }}", prop="content_hash").at(1220, 0)

    manifest = http(wf, "Index manifest (Qdrant scroll)", f"{QDRANT}/points/scroll", "POST",
                    json_body='={{ JSON.stringify({ limit: 10000, with_payload: ["metadata"], '
                              'with_vector: false }) }}',
                    full_response=True, never_error=True, timeout_ms=20000)
    manifest.at(1480, 0).retry(3, 1000).once()
    manifest.note("One call per run; a 404 (no collection yet) is data, not an error.")

    plan = code(wf, "Plan the reindex", PLAN_JS).at(1740, 0)
    plan.note("added / changed / unchanged / removed, by SHA-256 of the file text.")

    any_delete = if_(wf, "Stale chunks to delete?", [cond_num("={{ $json.delete_count }}", "gt", 0)]).at(2000, 0)
    drop = http(wf, "Delete stale chunks (Qdrant)", f"{QDRANT}/points/delete?wait=true", "POST",
                json_body='={{ JSON.stringify({ filter: { must: [ { key: "metadata.source", '
                          'match: { any: $json.delete_sources } } ] } }) }}',
                timeout_ms=60000).at(2260, -160).retry(3, 1000)
    drop.note("Deletes by payload filter, so a changed file never leaves an older copy behind.")

    any_embed = if_(wf, "Anything to embed?",
                    [cond_num("={{ $('Plan the reindex').first().json.embed_count }}", "gt", 0)]).at(2520, 0)

    create = http(wf, "Create collection if missing (Qdrant)", QDRANT, "PUT",
                  json_body="={{ JSON.stringify({ vectors: { size: $('Index settings').first().json.vector_size, "
                            "distance: 'Cosine' } }) }}",
                  full_response=True, never_error=True, timeout_ms=20000).at(2780, -160).retry(3, 1000)
    create.note("409 'already exists' is the normal answer from the second run on.")

    chunker = code(wf, "Chunk and batch", CHUNK_JS).at(3040, -160)
    chunker.note("Recursive character split (900 / 150), grouped into batches of 16 chunks.")
    embed = http(wf, "Embed batch (Ollama)", OLLAMA_EMBED, "POST",
                 json_body=f'={{{{ JSON.stringify({{ model: "{EMBED_MODEL}", input: $json.inputs }}) }}}}',
                 timeout_ms=300000).at(3300, -160).retry(3, 3000)
    embed.note("One call per batch. Long timeout: Ollama runs one request at a time (OLLAMA_NUM_PARALLEL=1).")
    points = code(wf, "Build Qdrant points", POINTS_JS).at(3560, -160)
    points.note("Point id = file content hash + chunk index, so an upsert can never duplicate a chunk.")
    upsert = http(wf, "Upsert points (Qdrant)", f"{QDRANT}/points?wait=true", "PUT",
                  json_body="={{ JSON.stringify({ points: $json.points }) }}",
                  timeout_ms=120000).at(3820, -160).retry(3, 1000)
    upsert.note("Deterministic ids make this idempotent, so retrying is safe.")

    count = http(wf, "Count points (Qdrant)", QDRANT, "GET",
                 full_response=True, never_error=True, timeout_ms=20000).at(4080, 0).retry(3, 1000).once()
    summary = code(wf, "Index summary", INDEX_SUMMARY_JS).at(4340, 0)
    log_index = set_fields(wf, "Log input (index)", {
        "execution_id": "={{ $execution.id }}",
        "workflow_id": "={{ $workflow.id }}",
        "workflow_name": "={{ $workflow.name }}",
        "status": "={{ $json.status }}",
        "started_at": "={{ $('Index settings').first().json.started_at }}",
        "notes": "={{ $json.notes }}",
    }).at(4600, 0)
    log_index_call = execute_workflow(wf, "Log index run (P08)", catalog_id("P08"),
                                      cached_name="P08 - Log execution").at(4860, 0)

    wf.chain(reindex, cfg, read_docs)
    wf.connect(cfg, read_patterns)
    wf.connect(read_docs, corpus, inp=0)
    wf.connect(read_patterns, corpus, inp=1)
    wf.chain(corpus, text, prepare, hashed, manifest, plan, any_delete)
    wf.connect(any_delete, drop, out=0)
    wf.connect(any_delete, any_embed, out=1)
    wf.connect(drop, any_embed)
    wf.connect(any_embed, create, out=0)
    wf.connect(any_embed, count, out=1)              # nothing changed -> straight to the summary
    wf.chain(create, chunker, embed, points, upsert, count)
    wf.chain(count, summary, log_index, log_index_call)

    # ================================================================================================
    # QUERY PATH - the chat demo
    # ================================================================================================
    chat = chat_trigger(wf, "Ask the docs (chat)", public=True, title="Automation Lab docs",
                        subtitle="Grounded in docs/ and the pattern READMEs - it answers or it says it cannot.",
                        initial="Ask me about the Automation Lab: the patterns, the stack, the PRD or the ADRs.")
    chat.at(-340, 760)
    chat.parameters["options"]["responseMode"] = "responseNode"

    question = code(wf, "Prepare question", PREPARE_QUESTION_JS).at(-80, 760)
    question.note("top_k, the similarity floor and how many passages the model sees.")

    q_embed = http(wf, "Embed question (Ollama)", OLLAMA_EMBED, "POST",
                   json_body=f'={{{{ JSON.stringify({{ model: "{EMBED_MODEL}", input: $json.question }}) }}}}',
                   timeout_ms=120000).at(180, 760)
    q_embed.retry(2, 1500).on_error("continueErrorOutput")

    search = http(wf, "Search the docs (Qdrant)", f"{QDRANT}/points/search", "POST",
                  json_body="={{ JSON.stringify({ vector: $json.embeddings[0], "
                            "limit: $('Prepare question').first().json.top_k, with_payload: true }) }}",
                  full_response=True, never_error=True, timeout_ms=20000).at(440, 760)
    search.retry(2, 1500).on_error("continueErrorOutput")
    search.note("Read-only, so retrying is safe; 404 = the index was never built.")

    context = code(wf, "Build grounded context", BUILD_CONTEXT_JS).at(700, 760)
    enough = if_(wf, "Enough context?", [cond_bool("={{ $json.has_context }}")]).at(960, 760)

    answer = llm_chain(wf, "Answer from the docs (LLM)", "={{ $json.prompt }}", system=SYSTEM).at(1220, 660)
    answer.retry(2, 2000).on_error("continueErrorOutput")
    answer.note("One question per call, so the error lane is dependable.")
    chat_model = wf.add("Ollama (llama3.2:3b)", "@n8n/n8n-nodes-langchain.lmChatOllama", 1,
                        {"model": CHAT_MODEL,
                         "options": {"temperature": 0, "numCtx": 4096, "numPredict": 400, "keepAlive": "30m"}},
                        cred="ollama", pos=(1160, 900))
    chat_model.note("numCtx 4096 fits 4 passages; keepAlive 30m keeps the model warm between questions.")
    wf.attach(chat_model, answer, "ai_languageModel")

    no_context = code(wf, "No grounded answer", NO_CONTEXT_JS).at(1220, 1140)
    unusable = code(wf, "Model unavailable", MODEL_UNAVAILABLE_JS).at(1480, 660)
    unreachable = code(wf, "Retrieval failed", RETRIEVAL_FAILED_JS).at(440, 1000)

    reply = code(wf, "Reply", REPLY_JS).at(1740, 760)
    reply.note("Adds the Sources block and turns NOT_IN_DOCS into a plain refusal.")
    respond_chat = respond(wf, "Respond to chat", body="={{ $json }}").at(2000, 760)
    log_q = set_fields(wf, "Log input (question)", {
        "execution_id": "={{ $execution.id }}",
        "workflow_id": "={{ $workflow.id }}",
        "workflow_name": "={{ $workflow.name }}",
        "status": "={{ $('Reply').first().json.grounded ? 'success' "
                  ": (['model_error','index_error'].includes($('Reply').first().json.reason) ? 'error' "
                  ": 'warning') }}",
        "started_at": "={{ $('Prepare question').first().json.asked_at }}",
        "notes": "={{ $('Reply').first().json.reason + ' | q: ' + $('Reply').first().json.question "
                 "+ ' | ' + $('Reply').first().json.sources_used.length + ' source(s), top score ' "
                 "+ $('Reply').first().json.top_score }}",
    }).at(2260, 760)
    log_q_call = execute_workflow(wf, "Log question (P08)", catalog_id("P08"),
                                  cached_name="P08 - Log execution").at(2520, 760)
    log_q_call.on_error("continueRegularOutput")
    log_q_call.note("After the response: logging a question must never break the conversation.")

    wf.chain(chat, question, q_embed, search, context, enough)
    wf.connect(q_embed, unreachable, out=1)          # error lane of the embedding call
    wf.connect(search, unreachable, out=1)           # error lane of the vector search
    wf.connect(enough, answer, out=0)
    wf.connect(enough, no_context, out=1)
    wf.connect(answer, reply, out=0)
    wf.connect(answer, unusable, out=1)              # error lane of the chain
    wf.connect(no_context, reply)
    wf.connect(unusable, reply)
    wf.connect(unreachable, reply)
    wf.chain(reply, respond_chat, log_q, log_q_call)

    # ================================================================================================
    # canvas documentation
    # ================================================================================================
    wf.sticky(
        "## A01 - RAG over this repository's own docs\n"
        "**Top row - ingest** (manual, an operator action). `docs/**/*.md` + `patterns/*/README.md` -> text -> "
        "SHA-256 per file -> compare with what `lab_docs` already holds -> delete the chunks of changed and "
        "removed files -> chunk and embed only new and changed files with **nomic-embed-text** -> upsert -> "
        "one `execution_log` row.\n"
        "**Bottom row - query** (chat). Question -> embed -> top 6 chunks from Qdrant -> keep what clears the "
        "similarity floor -> **llama3.2:3b** answers *only* from those passages -> the reply carries them with "
        "their scores.\n\n"
        "Everything is local: Ollama + Qdrant, no API key, no cloud embeddings, ~8 GB RAM.\n"
        "Patterns: **P01** (error workflow), **P08** (one `execution_log` row per index run and per question).",
        pos=(-400, -540), width=1080, height=340)
    wf.sticky(
        "### Re-running does not duplicate vectors\n"
        "*Plan the reindex* compares the SHA-256 of each file with the `content_hash` stored on its chunks:\n"
        "* **new** -> embed\n"
        "* **changed** -> delete its chunks by payload filter, then embed\n"
        "* **removed from disk** -> delete its chunks\n"
        "* **unchanged** -> nothing at all\n\n"
        "A second run with no edits embeds 0 files and takes seconds. And because a point id is derived from "
        "`content_hash + chunk_index`, even a re-upsert of the same text overwrites the same points.\n"
        "`force_rebuild: true` in *Index settings* re-embeds everything.",
        pos=(1700, -600), width=620, height=400, color=3)
    wf.sticky(
        "### Why HTTP nodes and not the Qdrant Vector Store node\n"
        "`vectorStoreQdrant` bundles `@qdrant/js-client-rest`, which hands an **undici 6** Agent to Node 26's "
        "built-in `fetch` (undici 7). Every call dies with `TypeError: fetch failed` / "
        "`InvalidArgumentError: invalid onError method` - reproduced on this stack in both the main process "
        "and the CLI.\n\n"
        "So the vector store is assembled from HTTP + Code nodes against the Qdrant REST API, writing the "
        "**same payload shape** (`content` + `metadata`) the LangChain node would write. When the client is "
        "fixed upstream, the node drops back in over the same collection.",
        pos=(2740, -600), width=660, height=400, color=6)
    wf.sticky(
        "### Grounded, or it says so\n"
        "Three guards, in this order:\n"
        "1. **Similarity floor** (`min_score` in *Prepare question*): nothing close enough -> the model is never "
        "called.\n"
        "2. **The contract in the prompt**: answer only from the numbered passages, else reply `NOT_IN_DOCS`. "
        "*Reply* turns that marker into a plain refusal.\n"
        "3. **Citations**: only the passages the answer actually cites are printed, with their scores - so a "
        "reader can check the claim against the file.\n\n"
        "A failed search, a failed embedding call or an unusable model answer takes its own lane and still ends "
        "in a normal chat reply; the `execution_log` row carries the reason "
        "(`success` / `warning` / `error`).",
        pos=(-400, 380), width=1080, height=300, color=5)
    return wf


if __name__ == "__main__":
    build().save()
