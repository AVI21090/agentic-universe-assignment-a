# Submission

## Candidate

- **Name:** Avichal Jaiswal
- **Assignment:** A — Interaction Ledger & Reporting Pipeline
- **Focused time spent:** 5 hours

## Setup and run

Python 3.11 or later is required. From the repository root, use the commands for your shell. Keep the same `PII_HMAC_KEY` for both drops, and do not commit the key.

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:PII_HMAC_KEY = (python -c "import secrets; print(secrets.token_hex(32))").Trim()
python assignment-a-data\tools\generate_data.py --seed 42 --scale small --out data
python -m solution --db data/ledger.sqlite ingest data/drop_1
python -m solution --db data/ledger.sqlite ingest data/drop_2
python -m solution --db data/ledger.sqlite report --out reports
python -m pytest -q
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export PII_HMAC_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
python -m solution --db data/ledger.sqlite ingest data/drop_1
python -m solution --db data/ledger.sqlite ingest data/drop_2
python -m solution --db data/ledger.sqlite report --out reports
python -m pytest -q
```

## Design

The pipeline uses SQLite. A lead is identified by `workflow_id` and a purpose-separated HMAC of its canonical Indian phone number. The database stores only the phone's last four digits for masked reporting; customer names are stored as HMAC tokens. The HMAC key is read from the process environment.

Call events are stored in an append-only ledger with unique `event_id` values. Database triggers reject event updates and deletes. Phone-bearing events retain only the keyed phone token needed for lead matching. A `disposition.override` is linked through `workflow_id` and `call_id` to another event for that call.

Within each drop, lead files are processed before event files. Files are sorted by workflow path and filename, and rows retain their file order. Each drop is committed atomically. Identical files at an already processed path are skipped. Reports resolve orphan events against all leads currently stored, so an event from an earlier drop can become linked after a later lead arrives.

## Decisions and assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| Store keyed HMACs for PII and phone last four digits | Allows deterministic joins and masked reporting without storing raw PII | Reversible encryption, which would also require managing decryption |
| Use the first non-empty source column when multiple headers map to one field | Makes ambiguous aliases deterministic | Reject the file or use the last matching column |
| Use event ID to break equal-timestamp ties | Keeps latest-event selection stable | Depend on file order |
| Keep the first event received for a duplicate event ID | Preserves deduplication and the append-only ledger | Replace the earlier event |
| Round decimal amounts with `ROUND_HALF_UP` to two places | Provides an explicit monetary rounding rule | Python's default half-even rounding |
| Retain an event with an invalid or missing timestamp, but no timestamp value | The spec gives accepted timestamp formats but no invalid-time rejection rule | Treat the event as malformed |
| For conflicting leads with the same workflow and call ID, link an override to the lowest lead database ID | Keeps ambiguous linkage deterministic; supplied data uses unique call IDs | Drop the override as ambiguous |

## Failure handling

- Blank lead rows are skipped. Required-field failures are rejected with one reason; invalid optional values become null and increment field warnings.
- Malformed event JSON and events without IDs are counted and skipped. Duplicate event IDs are counted and dropped.
- Orphans remain in the ledger and can become linked after later lead ingestion.
- Each drop is transactional, so a failed ingestion does not leave a partially applied drop.
- Ingestion requires a stable `PII_HMAC_KEY`. Losing or changing the key prevents matching new inputs to existing tokens.
- Invalid event timestamps remain stored but are omitted from date-grouped reports and `last_call_at`.
- The implementation targets the supplied schema and event contract; it does not include a general schema-migration framework or production key rotation.

## Verification and results

- **Local Windows run:** Python 3.13.5; `python -m pytest -q` passed all 23 tests. I generated the seed-43 small dataset, ingested both drops sequentially, and generated all five report files.
- **Additional checks with ChatGPT assistance:** Small datasets with seeds 42 and 43 were checked using sequential and combined-drop ingestion; all five report files matched for each seed. A database inspection found no raw phone numbers or customer names in lead or event fields.
- The seed-42 small run processed 2,057 lead rows and 8,745 event lines. The seed-43 run processed 2,044 lead rows and 9,131 event lines. The large-scale performance goal has not been measured.

## If I had another day

1. Run the pipeline against more fresh seeds and investigate any output mismatches.
2. Measure the large dataset and optimize if needed.
3. Add integration tests for duplicate batches, malformed event lines, unknown dispositions, and events arriving before leads.
4. Document a production scheduling, failure-recovery, and backfill plan, and consider key rotation.

## Submission channel

Keep the repository private. Use the submission form and instructions in the recruiter email after completing the required video assessment. Do not use placeholder reviewer handles or email addresses from the assignment README; use only contact details actually provided by the hiring team.