# A03 test inputs

| file | what it is |
|---|---|
| `meeting-standup.txt` | Synthetic English standup transcript (invented people, `@lab.local` addresses). Not audio: it is the *content* the summariser and the extractor are meant to handle - several action items with owners and spoken due dates ("today", "tomorrow morning", "by Friday", "before the end of the week"), two decisions, one blocker. |
| `asr-response.json` | The real `POST /asr?output=json` body for `seed/files/meeting-clip.wav`, captured from the running `whisper` container. This is the shape **Normalize transcript** parses (`{language, segments[], text}`, segments are `dataclasses.asdict(faster_whisper.Segment)`). |
| `expected.json` | What a full run produced on the reference machine: the quality-gate numbers, the summary, the extracted task list and the `documents` / `tasks` rows. |

The audio itself is **not** copied here - it is `seed/files/meeting-clip.wav`, mounted read-only into n8n at
`/home/node/.n8n-files/seed/meeting-clip.wav`, which is what **Config** points at.

## Replay

```bash
# raw ASR call (what the HTTP node does)
curl -s -X POST "localhost:9010/asr?encode=true&task=transcribe&language=en&vad_filter=false&word_timestamps=false&output=json" \
     -F "audio_file=@seed/files/meeting-clip.wav" > /tmp/asr.json
python -m json.tool < /tmp/asr.json | head -40

# the workflow end to end
python scripts/dev/run-workflow.py A03
cat data/out/meeting-*.md

# the two model calls, without audio, using the synthetic transcript
docker compose exec -T ollama ollama run llama3.2:3b \
  "Summarize this meeting in 3-6 bullet points: $(cat workflows/A03-audio-to-tasks/test/meeting-standup.txt)"
```
