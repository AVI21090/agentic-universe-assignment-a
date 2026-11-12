# Assignment A solution: Interaction Ledger and Reporting Pipeline

This solution ingests lead CSV files and call-event JSONL drops into SQLite, then writes the five reports required by Assignment A. It uses Python 3.11+ and the Python standard library for the pipeline. The repository's existing requirements file also supports the Assignment B mock provider and pytest.

## PII protection

The pipeline never stores a raw phone number or customer name. It stores purpose-separated HMAC-SHA256 tokens using the PII_HMAC_KEY environment variable. The last four phone digits are kept only to produce the required masked report value. The key is not written to the database or repository. Use the same secret for every drop stored in one database.

Generate a secret once and keep it private:

~~~bash
python -c "import secrets; print(secrets.token_hex(32))"
~~~

For macOS/Linux, set the generated value in the shell:

~~~bash
export PII_HMAC_KEY="paste-the-generated-value-here"
~~~

For PowerShell:

~~~powershell
$env:PII_HMAC_KEY = "paste-the-generated-value-here"
~~~

Then use the same secret for both drops. The command below assumes you are still in the same PowerShell session:

~~~powershell
python assignment-a-data\tools\generate_data.py --seed 42 --scale small --out data
python -m solution --db data/ledger.sqlite ingest data/drop_1
python -m solution --db data/ledger.sqlite ingest data/drop_2
python -m solution --db data/ledger.sqlite report --out reports
~~~

## Setup

Run these commands from the repository root:

~~~bash
python -m venv .venv
~~~

Activate the environment:

~~~bash
source .venv/bin/activate
~~~

On Windows PowerShell, use:

~~~powershell
.venv\Scripts\Activate.ps1
~~~

Install the repository dependencies:

~~~bash
pip install -r requirements.txt
~~~

## Generate and ingest data

Generate the supplied synthetic dataset. The generated data and database are ignored by Git:

~~~bash
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
python -m solution --db data/ledger.sqlite ingest data/drop_1
python -m solution --db data/ledger.sqlite ingest data/drop_2
python -m solution --db data/ledger.sqlite report --out reports
~~~

The drop paths are processed in the order supplied. Each drop is transactional; identical files at the same path are skipped on re-ingest. Event orphan status is calculated when reports are written, so events received before their lead become linked after a later drop adds that lead.

To process both drops in one invocation, pass them in order:

~~~bash
python -m solution --db data/ledger.sqlite ingest data/drop_1 data/drop_2
~~~

To use a different seed, generate it into a separate folder and ingest its drops in order. Do not commit generated data, database files, or reports.

## Reports

The report command writes:

- batch_summary.csv
- daily_overview.csv
- lead_report.csv
- rejections.csv
- data_quality.json

CSV headers and sort order follow the assignment contract. Report dates are IST dates. PII is excluded from rejection rows and masked in lead_report.csv.

## Tests

The required test suite is under tests and can be run with one command:

~~~bash
python -m pytest -q
~~~

The tests cover phone formats, date formats and invalid dates, currency parsing, disposition override precedence, lead buckets, late event linking, and repeated-ingest idempotency.

## Design notes

- SQLite stores leads, an immutable event ledger, rejected-row metadata, processed-file fingerprints, and quality counters.
- Lead identity is a keyed HMAC of the canonical phone plus workflow_id. Raw PII is not persisted.
- Event IDs are unique. The first valid event with an ID is retained; later deliveries with the same ID are counted and dropped.
- Leads merge in drop, filename, and row order. The first batch remains attached to the lead; later non-null fields update it.
- Reports are derived from the ledger. Known overrides take precedence over disposition.set events, and the latest event of the selected type wins.
- Unknown or orphan disposition events are excluded from call outcome reports. Their quality counts are computed after linking against all current leads.


