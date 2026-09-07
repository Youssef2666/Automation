#!/usr/bin/env python
"""A03 - Audio to Transcript to Summary to Task List (all local: Whisper + Ollama).

Manual/CLI -> Config (audio file, language, quality gate) -> Read audio -> Transcribe (Whisper ASR web service,
multipart POST /asr?output=json, faster_whisper engine) -> Normalize transcript (Code: parse the text/plain JSON
body, keep the segments, compute word count + mean no_speech_prob / avg_logprob) -> Usable transcript?
  yes -> Transcript for the model (Set) -> Summarize meeting (Summarization Chain, map-reduce, Ollama)
       -> Collect summary (Code) -> Extract action items (Information Extractor, JSON schema, Ollama)
       -> Build task list (Code: due dates resolved against the meeting date, dedupe, cap)
  no  -> No usable speech (skip AI) (Code: same output contract, status low_confidence)
-> Record transcript (documents row, meta = whole record) -> Replace task list (one idempotent CTE:
delete this source's rows, insert the new ones from a JSON array) -> Build meeting notes -> Write meeting notes
(data/out/meeting-<run_id>.md) -> Log input -> Log execution (P08).

Patterns: P01 (error workflow), P08 (one execution_log row per run).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, catalog_id, code, cond_bool, cond_exists, execute_workflow, http,  # noqa: E402
                         if_, info_extractor, manual_trigger, ollama_chat, postgres_insert, postgres_query,
                         read_file, set_fields, stop_error, summarize_chain, write_file)

# ---------------------------------------------------------------------------------------------------
# Whisper ASR web service (onerahmet/openai-whisper-asr-webservice v1.10.0, ASR_ENGINE=faster_whisper).
# Verified against the running container's own source (/app/app/webservice.py + /app/app/utils.py):
#   POST /asr, multipart field name `audio_file`, query encode|task|language|vad_filter|word_timestamps|output.
#   Every response is a StreamingResponse with media_type "text/plain" - including output=json - so the HTTP
#   Request node uses responseFormat: text and the body arrives as a JSON *string* in $json.data.
#   output=json body: {"language": "en", "segments": [dataclasses.asdict(faster_whisper.Segment)], "text": "..."}
#   Segment fields: id, seek, start, end, text, tokens, avg_logprob, compression_ratio, no_speech_prob,
#   words, temperature. `tokens` is long and useless downstream, so it is dropped here.
# ---------------------------------------------------------------------------------------------------
NORMALIZE_JS = r"""
const cfg = $('Config').first().json;
const raw = $input.first().json.data;
let asr;
try { asr = typeof raw === 'string' ? JSON.parse(raw) : raw; }
catch (e) {
  throw new Error('A03: /asr?output=json did not return JSON (got ' + String(raw).slice(0, 160) + ')');
}
if (!asr || typeof asr !== 'object' || !('text' in asr)) {
  throw new Error('A03: unexpected ASR body, no "text" key: ' + JSON.stringify(asr).slice(0, 200));
}

const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : null);
const mean = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
const round = (v, d = 3) => (v === null ? null : Math.round(v * 10 ** d) / 10 ** d);

const segments = (Array.isArray(asr.segments) ? asr.segments : []).map((s) => ({
  id: num(s.id), start: round(num(s.start), 2), end: round(num(s.end), 2),
  text: String(s.text ?? '').trim(),
  avg_logprob: round(num(s.avg_logprob)), no_speech_prob: round(num(s.no_speech_prob)),
  compression_ratio: round(num(s.compression_ratio)),
}));
const text = String(asr.text ?? '').replace(/\s+/g, ' ').trim();
const word_count = text ? text.split(/\s+/).length : 0;
const audio_seconds = segments.length ? round(segments[segments.length - 1].end, 2) : 0;
const no_speech = round(mean(segments.map((s) => s.no_speech_prob).filter((v) => v !== null)));
const logprob = round(mean(segments.map((s) => s.avg_logprob).filter((v) => v !== null)));

// Quality gate. Whisper never says "I heard nothing": on silence or non-speech it emits plausible sentences.
// Four signals - length, the model's own two confidence numbers, and verbatim repetition, which is what a
// Whisper decoding loop looks like from the outside ("I'm sorry. I'm sorry. I'm sorry.").
const unique_segments = new Set(segments.map((s) => s.text.toLowerCase())).size;
const repetition = segments.length >= 3 ? round(1 - unique_segments / segments.length, 2) : 0;
const reasons = [];
if (word_count < Number(cfg.min_words)) reasons.push(`only ${word_count} words (min ${cfg.min_words})`);
if (no_speech !== null && no_speech > Number(cfg.max_no_speech)) {
  reasons.push(`mean no_speech_prob ${no_speech} > ${cfg.max_no_speech}`);
}
if (logprob !== null && logprob < Number(cfg.min_avg_logprob)) {
  reasons.push(`mean avg_logprob ${logprob} < ${cfg.min_avg_logprob}`);
}
if (repetition > Number(cfg.max_repetition)) {
  reasons.push(`${Math.round(repetition * 100)}% of the ${segments.length} segments repeat verbatim`);
}
if (!segments.length) reasons.push('no segments returned');

const file = String(cfg.audio_file);
const file_name = file.split('/').pop();
return [{ json: {
  run_id: cfg.run_id, started_at: cfg.started_at,
  audio_file: file, file_name, source: `a03:${file_name}`,
  meeting_title: cfg.meeting_title, meeting_date: cfg.meeting_date,
  language: asr.language ?? cfg.language, model: cfg.asr_model, engine: cfg.asr_engine,
  text, word_count, segment_count: segments.length, audio_seconds,
  mean_no_speech_prob: no_speech, mean_avg_logprob: logprob, repetition,
  usable: reasons.length === 0, quality_reasons: reasons, segments,
} }];
"""

MAP_PROMPT = (
    "Below is part of the transcript of a team meeting. Write 2-4 short factual sentences covering what was "
    "said in it. Keep every name, date, number and commitment exactly as spoken. Do not invent anything.\n\n"
    "\"{text}\"\n\nPARTIAL SUMMARY:"
)
COMBINE_PROMPT = (
    "You are writing the recap of a team meeting for a colleague who missed it. Using only the partial "
    "summaries below, write 3 to 6 short bullet points, one line each, starting with \"- \": what was "
    "discussed, what was decided, what is blocked. No preamble, no closing sentence, no invented facts.\n\n"
    "\"{text}\"\n\nMEETING SUMMARY:"
)

COLLECT_SUMMARY_JS = r"""
// Summarization Chain output key has moved between versions ({response:{text}} / {text} / {output_text}), so
// take the first string we recognise instead of trusting one shape.
const src = $input.first().json ?? {};
const pick = (v) => (typeof v === 'string' ? v : (v && typeof v === 'object' && typeof v.text === 'string' ? v.text : null));
const summary = (pick(src.response) ?? pick(src.text) ?? pick(src.output_text) ?? pick(src.output) ?? '').trim();
if (!summary) {
  throw new Error('A03: the summarization chain returned no text (keys: ' + Object.keys(src).join(', ') + ')');
}
const rec = $('Normalize transcript').first().json;
// One prompt for the extractor: the summary gives it the shape of the meeting, the transcript keeps it honest.
const extract_input = [
  `Meeting: ${rec.meeting_title} on ${rec.meeting_date}.`,
  'Extract every action item somebody committed to, and every decision that was made.',
  'Give every action item a short imperative title and the name of the person who took it on.',
  'Copy "due" exactly as it was spoken (today, tomorrow morning, by Friday, before the end of the week);',
  'leave the field out when no deadline was mentioned. Never invent a date.',
  '',
  'SUMMARY:', summary,
  '',
  'TRANSCRIPT:', rec.text,
].join('\n');
return [{ json: { summary, summary_lines: summary.split('\n').map((l) => l.trim()).filter(Boolean),
                  extract_input } }];
"""

TASK_SCHEMA_EXAMPLE = {
    "tasks": [
        {"title": "Send the revised quote to the customer",
         "owner": "Dana",
         "due": "Friday",
         "priority": "high"}
    ],
    "decisions": ["Ship the pricing change behind a feature flag"],
}

BUILD_TASKS_JS = r"""
// Information Extractor -> {output: {tasks, decisions}}; with onError=continueRegularOutput a failed extraction
// arrives as {error: {...}} instead, and the run degrades to "transcript + summary, no tasks" (status warning).
const rec = $('Normalize transcript').first().json;
const cfg = $('Config').first().json;
const sum = $('Collect summary').first().json;
const src = $input.first().json ?? {};
const out = src.output && typeof src.output === 'object' ? src.output : null;
const extraction_ok = !!out && Array.isArray(out.tasks);
const extraction_error = extraction_ok ? null : String(src.error?.message ?? src.error ?? 'no structured output').slice(0, 300);

const WEEKDAYS = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];
const DAY = 86400000;
// Due dates are resolved here, not by the model: a 3B model cannot do calendar arithmetic reliably, and a wrong
// date in a task list is worse than no date. Anything we cannot resolve stays as `due_raw`.
function resolveDue(raw, baseDate) {
  if (raw === null || raw === undefined) return null;
  const s = String(raw).trim().toLowerCase();
  if (!s || ['none', 'n/a', 'na', 'tbd', 'unspecified', 'unknown', 'null'].includes(s)) return null;
  const iso = s.match(/\d{4}-\d{2}-\d{2}/);
  if (iso) return iso[0];
  const base = new Date(`${baseDate}T00:00:00Z`);
  if (Number.isNaN(base.getTime())) return null;
  const add = (d) => new Date(base.getTime() + d * DAY).toISOString().slice(0, 10);
  if (/\btoday\b/.test(s)) return add(0);
  if (/\btomorrow\b/.test(s)) return add(1);
  const inN = s.match(/\bin (\d{1,2}) (day|week)s?\b/);
  if (inN) return add(Number(inN[1]) * (inN[2] === 'week' ? 7 : 1));
  const wd = WEEKDAYS.findIndex((w) => new RegExp(`\\b${w}\\b`).test(s));
  if (wd >= 0) {
    let delta = (wd - base.getUTCDay() + 7) % 7;
    if (delta === 0) delta = 7;                       // "Friday" said on a Friday means the next one
    if (/\bnext\b/.test(s)) delta += 7;
    return add(delta);
  }
  if (/\bend of (the )?week\b/.test(s)) return add(((5 - base.getUTCDay() + 7) % 7) || 7);
  if (/\bnext week\b/.test(s)) return add(7);
  return null;
}

const PRIORITIES = ['low', 'medium', 'high'];
const seen = new Set();
const tasks = [];
for (const t of (extraction_ok ? out.tasks : [])) {
  if (!t || typeof t !== 'object') continue;
  const title = String(t.title ?? t.task ?? '').replace(/\s+/g, ' ').trim().slice(0, 200);
  if (!title || title.length < 4) continue;
  const key = title.toLowerCase();
  if (seen.has(key)) continue;                        // the same commitment is often restated in a meeting
  seen.add(key);
  const ownerRaw = String(t.owner ?? '').replace(/\s+/g, ' ').trim();
  const owner = ownerRaw && !/^(none|n\/a|na|unassigned|unknown|team)$/i.test(ownerRaw) ? ownerRaw.slice(0, 80) : null;
  const dueRaw = t.due ?? t.due_date ?? null;
  const priority = PRIORITIES.includes(String(t.priority ?? '').toLowerCase()) ? String(t.priority).toLowerCase() : 'medium';
  tasks.push({ title, owner, due_date: resolveDue(dueRaw, rec.meeting_date), due_raw: dueRaw ? String(dueRaw).slice(0, 80) : null,
               priority, source: rec.source });
  if (tasks.length >= Number(cfg.max_tasks)) break;
}
const decisions = (extraction_ok && Array.isArray(out.decisions) ? out.decisions : [])
  .map((d) => String(d ?? '').replace(/\s+/g, ' ').trim()).filter((d) => d.length > 3).slice(0, 20);

const status = extraction_ok ? 'ok' : 'extraction_failed';
const meta = {
  run_id: rec.run_id, source: rec.source, status,
  meeting: { title: rec.meeting_title, date: rec.meeting_date },
  audio: { file: rec.audio_file, file_name: rec.file_name, seconds: rec.audio_seconds, language: rec.language },
  asr: { engine: rec.engine, model: rec.model, word_count: rec.word_count, segment_count: rec.segment_count,
         mean_no_speech_prob: rec.mean_no_speech_prob, mean_avg_logprob: rec.mean_avg_logprob,
         repetition: rec.repetition },
  quality: { usable: true, reasons: [] },
  transcript: rec.text, segments: rec.segments,
  summary: sum.summary, summary_lines: sum.summary_lines,
  decisions, tasks, task_count: tasks.length,
  extraction_ok, extraction_error,
  started_at: rec.started_at, finished_at: new Date().toISOString(),
};
return [{ json: { status, file_name: rec.file_name, storage_key: rec.audio_file, meta,
                  task_count: tasks.length, transcribed_words: rec.word_count } }];
"""

SKIP_JS = r"""
// Same output contract as "Build task list", so both lanes feed the same persistence chain. Nothing is sent to
// the LLM: summarising a transcript the ASR itself is not confident about only produces confident nonsense.
const rec = $input.first().json;
const meta = {
  run_id: rec.run_id, source: rec.source, status: 'low_confidence',
  meeting: { title: rec.meeting_title, date: rec.meeting_date },
  audio: { file: rec.audio_file, file_name: rec.file_name, seconds: rec.audio_seconds, language: rec.language },
  asr: { engine: rec.engine, model: rec.model, word_count: rec.word_count, segment_count: rec.segment_count,
         mean_no_speech_prob: rec.mean_no_speech_prob, mean_avg_logprob: rec.mean_avg_logprob,
         repetition: rec.repetition },
  quality: { usable: false, reasons: rec.quality_reasons },
  transcript: rec.text, segments: rec.segments,
  summary: '', summary_lines: [], decisions: [], tasks: [], task_count: 0,
  extraction_ok: false, extraction_error: null,
  started_at: rec.started_at, finished_at: new Date().toISOString(),
};
return [{ json: { status: 'low_confidence', file_name: rec.file_name, storage_key: rec.audio_file, meta,
                  task_count: 0, transcribed_words: rec.word_count } }];
"""

# One statement, so a replay is idempotent: the previous rows of this source disappear and the current task list
# takes their place. json_populate_recordset casts the ISO strings to `date` and ignores the extra keys
# (priority, due_raw, source) that the `tasks` table does not have.
REPLACE_TASKS_SQL = """with cleared as (
  delete from tasks where source = $1 returning 1
), incoming as (
  select * from json_populate_recordset(null::tasks, $2::json)
)
insert into tasks (source, title, owner, due_date)
select $1, title, owner, due_date from incoming
returning id, source, title, owner, due_date"""

NOTES_JS = r"""
// $json here is whatever the tasks statement returned (rows, or {} when there were none) - read the record from
// the documents row instead, and count the inserted task ids from this node's input.
const doc = $('Record transcript (documents)').first().json;
const meta = typeof doc.meta === 'string' ? JSON.parse(doc.meta) : doc.meta;
const inserted = $input.all().map((i) => i.json).filter((r) => r && r.id);
const cfg = $('Config').first().json;

const line = (t) => `- [ ] ${t.title}` +
  (t.owner ? ` - **${t.owner}**` : '') +
  (t.due_date ? ` - due ${t.due_date}` : (t.due_raw ? ` - due ${t.due_raw} (not resolved)` : '')) +
  (t.priority && t.priority !== 'medium' ? ` (${t.priority})` : '');
const md = [
  `# ${meta.meeting.title} - ${meta.meeting.date}`,
  '',
  `Source: \`${meta.audio.file_name}\` (${meta.audio.seconds}s, ${meta.audio.language}, ` +
  `${meta.asr.engine}/${meta.asr.model}) - document #${doc.id} - run ${meta.run_id}`,
  '',
  '## Summary',
  meta.summary || '_No summary: the transcript did not pass the quality gate ' +
    `(${(meta.quality.reasons || []).join('; ')})._`,
  '',
  '## Decisions',
  ...(meta.decisions.length ? meta.decisions.map((d) => `- ${d}`) : ['_none recorded_']),
  '',
  `## Action items (${meta.tasks.length})`,
  ...(meta.tasks.length ? meta.tasks.map(line) : ['_none extracted_']),
  '',
  '## Transcript',
  meta.transcript || '_empty_',
  '',
].join('\n');

const status = meta.status === 'ok' ? 'success' : 'warning';
const notes = meta.status === 'ok'
  ? `transcribed ${meta.asr.word_count} words in ${meta.audio.seconds}s of audio, ${meta.decisions.length} decision(s), ` +
    `${inserted.length} task(s) written for source ${meta.source} (document #${doc.id})`
  : meta.status === 'low_confidence'
    ? `low-confidence transcript, summarisation skipped: ${(meta.quality.reasons || []).join('; ')} (document #${doc.id})`
    : `summary stored but action-item extraction failed: ${meta.extraction_error} (document #${doc.id})`;
const file_name = `meeting-${meta.run_id}.md`;
return [{
  json: { document_id: doc.id, run_id: meta.run_id, source: meta.source, status: meta.status, log_status: status,
          notes, file_name, out_dir: cfg.out_dir, summary: meta.summary, decisions: meta.decisions,
          tasks: meta.tasks, tasks_inserted: inserted.length, task_ids: inserted.map((r) => r.id),
          word_count: meta.asr.word_count, audio_seconds: meta.audio.seconds },
  binary: { data: await this.helpers.prepareBinaryData(Buffer.from(md, 'utf8'), file_name, 'text/markdown') },
}];
"""


def build() -> Workflow:
    wf = Workflow("A03", "audio-to-tasks", "Audio to Transcript, Summary and Task List", tags=["AI"],
                  error_workflow=catalog_id("P01"),
                  description="Local Whisper transcription of a meeting recording, a map-reduce summary and a "
                              "structured action-item list from Ollama, stored as a documents row + tasks rows.")

    trg = manual_trigger(wf, "Run once (manual / CLI)")
    cfg = set_fields(wf, "Config", {
        "started_at": "={{ $now.toISO() }}",
        "run_id": "={{ $now.toFormat('yyyyLLdd-HHmmss') }}",
        "audio_file": "/home/node/.n8n-files/seed/meeting-clip.wav",
        "out_dir": "/home/node/.n8n-files/data/out",
        "meeting_title": "Weekly ops standup",
        "meeting_date": "={{ $now.toFormat('yyyy-LL-dd') }}",
        "language": "en",
        "vad_filter": "false",
        "asr_engine": "faster_whisper",
        "asr_model": "base",
        "min_words": 12,
        "max_no_speech": 0.6,
        "min_avg_logprob": -1.0,
        "max_repetition": 0.5,
        "max_tasks": 25,
    })
    cfg.note("Everything tunable lives here: the recording, the ASR language and the four quality-gate limits "
             "(min_words / max_no_speech / min_avg_logprob / max_repetition). vad_filter is a query flag, "
             "hence the string.")

    rd = read_file(wf, "Read audio", "={{ $json.audio_file }}").always_output()
    found = if_(wf, "Audio file found?", [cond_exists("={{ $binary?.data?.fileName }}")])
    found.note("Read/Write File is a glob: a path that matches nothing returns zero items and the run would end "
               "green having done nothing. alwaysOutputData + this check turn that silence into a failure.")
    missing = stop_error(wf, "Missing recording",
                         "=A03: no file matched {{ $('Config').first().json.audio_file }}")
    asr = http(wf, "Transcribe (Whisper)", "http://whisper:9000/asr", method="POST",
               query={
                   "encode": "true",
                   "task": "transcribe",
                   "language": "={{ $('Config').first().json.language }}",
                   "vad_filter": "={{ $('Config').first().json.vad_filter }}",
                   "word_timestamps": "false",
                   "output": "json",
               },
               form_binary=("audio_file", "data"), response="text", timeout_ms=600000).retry(2, 5000)
    asr.note("POST /asr, multipart field `audio_file`. The service answers text/plain even for output=json, so "
             "responseFormat is text and the body arrives as a string in $json.data. 10 min timeout: `base` on "
             "CPU runs at roughly 1x-3x realtime; a retry re-transcribes the whole file, so only 2 tries.")
    norm = code(wf, "Normalize transcript", NORMALIZE_JS)
    gate = if_(wf, "Usable transcript?", [cond_bool("={{ $json.usable }}")])
    gate.note("Whisper hallucinates fluent sentences over silence and noise; word count, the model's own "
              "no_speech_prob / avg_logprob and verbatim segment repetition are the gate before anything "
              "reaches the LLM.")

    for_model = set_fields(wf, "Transcript for the model", {"text": "={{ $json.text }}"})
    for_model.note("The Summarization Chain feeds the whole item JSON to the model - so hand it the text only, "
                   "not the segments.")
    summ = summarize_chain(wf, "Summarize meeting", prompt=MAP_PROMPT, combine_prompt=COMBINE_PROMPT,
                           method="map_reduce", chunk_size=3000, chunk_overlap=200)
    summ.note("map-reduce: a two-hour transcript never fits a 3B context window, so chunk -> summarise each "
              "chunk -> combine. One chunk here, same code path.")
    summ_model = ollama_chat(wf, "Ollama (summary)", temperature=0.2)
    wf.attach(summ_model, summ, "ai_languageModel")
    collect = code(wf, "Collect summary", COLLECT_SUMMARY_JS)

    extract = info_extractor(wf, "Extract action items", "={{ $json.extract_input }}", TASK_SCHEMA_EXAMPLE)
    extract.retry(2, 3000).on_error("continueRegularOutput")
    extract.note("Structured output against a JSON schema. A 3B model does fail this sometimes - continue on "
                 "error, so the run still stores the transcript and summary and is logged as `warning`.")
    extract_model = ollama_chat(wf, "Ollama (extraction)", temperature=0)
    wf.attach(extract_model, extract, "ai_languageModel")
    tasks = code(wf, "Build task list", BUILD_TASKS_JS)
    tasks.note("Cleans the model's output into rows: titles trimmed and deduped, owner normalised, due dates "
               "resolved against the meeting date in code (never by the model).")
    # Pinned below the AI lane so it does not sit next to the "Ollama (summary)" sub-node on the canvas.
    skip = code(wf, "No usable speech (skip AI)", SKIP_JS).at(1560, 440)

    doc = postgres_insert(wf, "Record transcript (documents)", "documents", {
        "kind": "meeting-transcript",
        "file_name": "={{ $json.file_name }}",
        "storage_key": "={{ $json.storage_key }}",
        "meta": "={{ JSON.stringify($json.meta) }}",
    }, returning=True).retry(3, 1000)
    doc.note("One row per recording; meta carries the whole record (transcript, segments, summary, decisions, "
             "tasks, ASR confidence), so everything downstream reads the row instead of six earlier nodes.")
    save_tasks = postgres_query(wf, "Replace task list (tasks)", REPLACE_TASKS_SQL,
                                params="={{ [ $json.meta.source, JSON.stringify($json.meta.tasks) ] }}")
    save_tasks.retry(3, 1000).always_output()
    save_tasks.note("Idempotent replay: one statement deletes this source's previous tasks and inserts the "
                    "current list. alwaysOutputData so an empty task list still continues.")

    notes = code(wf, "Build meeting notes", NOTES_JS)
    write = write_file(wf, "Write meeting notes",
                       "={{ $('Config').first().json.out_dir }}/{{ $json.file_name }}")
    log_in = set_fields(wf, "Log input", {
        "execution_id": "={{ $execution.id }}",
        "workflow_id": "={{ $workflow.id }}",
        "workflow_name": "={{ $workflow.name }}",
        "status": "={{ $('Build meeting notes').first().json.log_status }}",
        "started_at": "={{ $('Config').first().json.started_at }}",
        "notes": "={{ $('Build meeting notes').first().json.notes }}",
    })
    log = execute_workflow(wf, "Log execution (P08)", catalog_id("P08"), cached_name="P08 - Log execution")

    wf.chain(trg, cfg, rd, found)
    wf.connect(found, asr, out=0)
    wf.connect(found, missing, out=1)
    wf.chain(asr, norm, gate)
    wf.connect(gate, for_model, out=0)
    wf.connect(gate, skip, out=1)
    wf.chain(for_model, summ, collect, extract, tasks, doc)
    wf.connect(skip, doc)
    wf.chain(doc, save_tasks, notes, write, log_in, log)

    wf.sticky(
        "## A03 - Audio -> transcript -> summary -> task list (100% local)\n"
        "**Whisper** (`whisper:9000`, faster_whisper `base`) transcribes the recording: multipart `POST /asr"
        "?output=json`, which answers *text/plain*, so the body is parsed in **Normalize transcript**.\n\n"
        "**Quality gate** first: word count, the model's own `no_speech_prob` / `avg_logprob`, and verbatim "
        "segment repetition. A transcript Whisper is not confident about skips the LLM entirely (status "
        "`low_confidence`) - summarising noise only produces confident nonsense.\n\n"
        "**Ollama** (`llama3.2:3b`) then does two jobs: a map-reduce **summary** (chunked, so a long meeting "
        "still fits) and an **Information Extractor** that returns `{tasks, decisions}` against a JSON schema. "
        "Due dates are resolved in code, not by the model.\n\n"
        "Output: a `documents` row (`kind = meeting-transcript`, meta = the whole record), `tasks` rows replaced "
        "idempotently per source, `data/out/meeting-<run_id>.md`, and one **P08** `execution_log` row "
        "(`success` / `warning`). Failures go to **P01**.",
        pos=(-40, -420), width=780, height=340)
    return wf


if __name__ == "__main__":
    build().save()
