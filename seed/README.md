# seed/ - synthetic demo data

Everything in this folder (and `docker/mock-api/db.json`) is produced by **one script**, `generate_seed.py`, with a
fixed random seed (`20260101`) and a fixed "now" (`2026-09-01T09:00:00Z`). Running it twice gives byte-identical
output, so the generated files are committed and CI can prove they are in sync with the generator.

**Synthetic only.** Every person, company, e-mail, phone number, invoice and receipt here is invented from word lists.
E-mails end in `@lab.local`, company domains in `.example.com`, the GitHub payloads use the fake repo
`lab-org/demo-repo`. Nothing is derived from a real client, employer, or person. Do not add real data.

## Regenerate

```bash
python seed/generate_seed.py                 # everything (SQL, files, payloads, mock-api db.json)
python seed/generate_seed.py --mock-api      # only docker/mock-api/db.json
python seed/generate_seed.py --only sql      # sql | files | payloads | mock-api (repeatable)
python seed/generate_seed.py --check         # no writes: compare disk with generator output, sanity-check the SQL
python seed/generate_seed.py --no-optional-deps   # force the pure-stdlib fallbacks
```

Edit the generator, regenerate, commit both the script and its outputs. Never hand-edit `schema.sql`, `seed.sql`,
`db.json` or the files below. To reload the running demo database: `bash scripts/reseed.sh`.

### Optional Python packages

The script needs only the standard library (Python 3.10+). These extras improve some binary files:

```bash
python -m pip install --user pillow openpyxl reportlab arabic-reshaper python-bidi
```

| package | used for | without it |
|---|---|---|
| `pillow` | `receipt-01.png`, `receipt-02.png`, `receipt-ar-01.png` | receipts are skipped; a `*.missing.txt` sidecar explains |
| `openpyxl` | `orders.xlsx` | a minimal but valid xlsx is built with `zipfile` (inline strings) |
| `reportlab` | `invoice-locked.pdf` (table drawn with grid lines) | a minimal hand-written PDF with real text objects |
| `arabic-reshaper` + `python-bidi` | glyph shaping for the Arabic receipt when Pillow has **no** libraqm | unshaped Arabic (letters not joined), flagged in the run output |

Pillow wheels from PyPI ship with libraqm, which shapes Arabic by itself; the generator detects that and does not
pre-shape (double shaping would mirror the text). The Arabic font is `docker/docgen/fonts/Amiri-Regular.ttf` (OFL).

## What is generated

### Database (`schema.sql`, `seed.sql`) - Postgres database `demo`

Loaded on first boot by `docker/postgres/init/01-demo-db.sh`. `schema.sql` drops and recreates every table
(`DROP TABLE IF EXISTS ... CASCADE` then `CREATE TABLE IF NOT EXISTS`), so both files are safe to re-run.
Money is `numeric(10,2)`, timestamps `timestamptz`, JSON `jsonb`; identity sequences are reset with `setval()` after
the inserts so workflows can insert without id collisions.

| table | rows | notes |
|---|---|---|
| customers | 60 | `CUST-0001..`, segments smb/mid/enterprise, companies from the mock-api `companies` list |
| products | 40 | `SKU-0001..`, 5 categories, some out of stock |
| orders | 200 | last 120 days, statuses pending/paid/shipped/cancelled/refunded, totals = sum of items |
| order_items | ~440 | 1-4 lines per order |
| employees | 25 | English + Arabic `full_name_ar` / `department_ar` |
| attendance | 300 | 25 employees x 12 working days (2026-08-13 .. 2026-08-28), present/late/absent/leave |
| tickets | 80 | last 45 days, SLA due from priority, a few Arabic bodies |
| leads | 40 | `domain` column added for enrichment; `enriched` jsonb filled once contacted |
| bookings | 30 | 8 start ~24 h after the fixed now (2026-09-02 ~09:00Z, for B02 reminders), 6 later, 16 in the past |
| expenses | 20 | rows 1-3 match the three receipt images (merchant, amount, currency, `receipt_file`) |
| rate_history | 30 | USD -> EUR/GBP/LYD, 10 daily points each |
| uptime_state | 3 | `mock-api`, `n8n`, `minio` all `up` |
| sync_state | 2 | `mock-api-events` cursor `0`; `mock-api-orders` cursor `1970-01-01T00:00:00Z` |
| uptime_checks, webhook_events, products_mirror, execution_log, notifications, content_variants, tasks, documents | 0 | schema only, filled by workflows |

### Files (`files/`, mounted read-only at `/home/node/.n8n-files/seed/`)

| file | content | for |
|---|---|---|
| `customers.csv` | 60 **new** customers (`CSV-0001..`, distinct from the DB rows so an import does not collide) with 3 deliberately broken rows: row 12 bad e-mail (no `@`), row 27 missing name, row 51 duplicate `external_id` of row 8 | D01 row-level validation |
| `orders.xlsx` | sheets `Orders` (40 orders `ORD-2026-9001..`) and `Items` (87 lines incl. 2 broken: unknown `SKU-9999`, negative qty) | D01 XLSX path |
| `invoice-locked.pdf` | text-based invoice `INV-2026-0142` with a 6-row line-items table, subtotal, VAT, total ("locked" = the only copy of the data is this PDF) | R04 table extraction |
| `receipt-01.png`, `receipt-02.png` | English receipts, 720 px wide, black on white (Tesseract-friendly); totals 23.10 LYD and 82.11 EUR | B03 OCR |
| `receipt-ar-01.png` | Arabic receipt in Amiri, Western digits for amounts; total 28.50 LYD | A04 Arabic OCR |
| `meeting-clip.wav` | ~30 s, 16 kHz mono 16-bit PCM of **synthetic speech-like audio** (voiced syllables with pitch contour, unvoiced bursts, word/sentence pauses). It is not real speech and has no transcript; it exists so the A03 pipeline has a valid, realistically sized WAV to push through Whisper. Replace with a real recording if you want a meaningful transcript | A03 transcription |
| `article.md` | ~980-word synthetic article, "Automation in small businesses" | A06 repurposing |
| `attendance-week.csv` | the last 5 working days of the attendance table (125 rows) with employee names and departments | R03 alternative input |

### Webhook payloads (`payloads/`)

| file | shape |
|---|---|
| `t01-order.json` | valid order: `external_id`, `order_number`, `customer{external_id,email,name}`, `items[]`, `total`, `ordered_at` |
| `t01-invalid.json` | wrong types / missing fields, must produce a 400 |
| `p03-replay.json` | same `external_id` as `t01-order.json` with `replay: true` (must be acknowledged, not re-processed) |
| `github-push.json` | GitHub push event for `lab-org/demo-repo` (abbreviated 12-char commit ids on purpose) |
| `github-issue.json` | GitHub `issues.opened` event |
| `m02-release.json` | GitHub `release.published` event (`v1.4.0`) |
| `b01-lead.json` | website lead with `company_domain: acme.example.com` (enrichable via `/companies/lookup`) |
| `b02-booking.json` | booking starting 24 h after the fixed now, `Africa/Tripoli` |

### mock-api dataset (`docker/mock-api/db.json`)

| collection | rows | notes |
|---|---|---|
| products | 40 | identical to the DB table |
| customers | 60 | identical to the DB table |
| orders | 120 | first 120 DB orders plus `updated_at` spread over the last 30 days (T02/T03 polling) |
| events | 300 | ids 1..300, `type` in `order.created` / `order.paid` / `ticket.opened`, ascending `created_at` |
| companies | 30 | keyed by `domain` (`<slug>.example.com`), with name/industry/size/country/city/founded |
| posts | 25 | blog posts with `title`, `slug`, `summary`, `excerpt`, `body`, `tags`, `author`, `published_at` (feeds `/feed.xml`) |

FX rates are computed in `middleware.js` and need no collection.
