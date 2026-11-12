from __future__ import annotations

import csv
import json
from dataclasses import dataclass

import pytest

from solution.config import load_dispositions, load_schemas
from solution.database import connect
from solution.ingest import ingest_drop
from solution.logic import latest_disposition, lead_bucket
from solution.normalization import normalize_phone, parse_date, parse_decimal
from solution.reports import write_reports


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("9876543210", "+919876543210"),
        ("+91 98765 43210", "+919876543210"),
        ("91-98765-43210", "+919876543210"),
        ("09876543210", "+919876543210"),
        ("(+91) 9876543210", "+919876543210"),
    ],
)
def test_phone_normalisation(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["", "N/A", "022-2345-6789", "5876543210", "123456789"])
def test_invalid_phone(raw):
    assert normalize_phone(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-12-01", "2026-12-01"),
        ("01/12/2026", "2026-12-01"),
        ("01-12-2026", "2026-12-01"),
        ("01-Dec-2026", "2026-12-01"),
        ("01-dEc-2026", "2026-12-01"),
    ],
)
def test_supported_dates(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw", ["31/02/2026", "soon", "2026-13-01"])
def test_invalid_dates(raw):
    assert parse_date(raw) is None


def test_dates_require_zero_padded_components():
    assert parse_date("2026-1-2") is None
    assert parse_date("1/02/2026") is None


def test_currency_decimal_parsing_and_rounding():
    assert parse_decimal("₹12,345") == "12345.00"
    assert parse_decimal("Rs. 1,234.567") == "1234.57"
    assert parse_decimal("INR 10.1") == "10.10"
    assert parse_decimal("-1") is None
    assert parse_decimal("twelve thousand") is None


def test_override_precedence_and_latest_override():
    sets = [(20, "evt_set_2", "not_interested"), (10, "evt_set_1", "renewed")]
    overrides = [
        (15, "evt_override_1", "callback_requested"),
        (30, "evt_override_2", "renewed"),
    ]
    assert latest_disposition(sets, overrides) == "renewed"
    assert latest_disposition(sets, []) == "not_interested"


@dataclass
class FakeCall:
    effective_disposition: str | None = None
    telephony_failed: bool = False


def test_bucket_priority_and_telephony_failure_rule():
    dispositions = load_dispositions()["motor_renewal"]
    assert lead_bucket([], dispositions) == "yet_to_reach_out"
    assert lead_bucket([FakeCall("renewed", True)], dispositions) == "completed"
    assert lead_bucket([FakeCall(None, True), FakeCall(None, True)], dispositions) == (
        "in_progress_telephony_failed"
    )
    assert lead_bucket([FakeCall(None, True), FakeCall(None, False)], dispositions) == (
        "in_progress_dialed"
    )


def test_orphan_event_links_after_later_lead_and_reingest_is_idempotent(tmp_path):
    schemas = load_schemas()
    dispositions = load_dispositions()
    key = b"test-key-with-more-than-16-bytes"

    first_drop = tmp_path / "drop_1"
    events_dir = first_drop / "events"
    events_dir.mkdir(parents=True)
    events = [
        {
            "event_id": "evt_1",
            "event_type": "call.initiated",
            "workflow_id": "motor_renewal",
            "call_id": "call_1",
            "occurred_at": "2026-10-05 10:00:00",
            "phone": "+91 98765 43210",
            "payload": {},
        },
        {
            "event_id": "evt_2",
            "event_type": "disposition.set",
            "workflow_id": "motor_renewal",
            "call_id": "call_1",
            "occurred_at": "2026-10-05T10:05:00+05:30",
            "phone": "09876543210",
            "payload": {"disposition_code": "renewed"},
        },
    ]
    (events_dir / "events_0001.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )

    second_drop = tmp_path / "drop_2"
    leads_dir = second_drop / "leads" / "motor_renewal"
    leads_dir.mkdir(parents=True)
    with (leads_dir / "batch_003.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow([
            "customer_name", "phone", "policy_number", "policy_expiry",
            "premium_amount", "preferred_language",
        ])
        writer.writerow([
            "Test Customer", "+91 98765 43210", "MOT/2026/100001",
            "2026-12-01", "Rs. 1000", "en",
        ])

    connection = connect(":memory:")
    try:
        ingest_drop(connection, first_drop, schemas, key)
        first_quality = write_reports(
            connection, tmp_path / "reports_after_first", schemas, dispositions
        )
        assert first_quality["orphan_events"] == 2

        ingest_drop(connection, second_drop, schemas, key)
        quality = write_reports(
            connection, tmp_path / "reports_final", schemas, dispositions
        )
        assert quality["orphan_events"] == 0
        assert quality["events_total"] == 2
        assert quality["lead_rows_total"] == 1
        assert quality["duplicate_lead_rows_merged"] == 0

        # Re-reading identical files from the same paths must not change counters.
        ingest_drop(connection, first_drop, schemas, key)
        ingest_drop(connection, second_drop, schemas, key)
        repeated = write_reports(
            connection, tmp_path / "reports_repeated", schemas, dispositions
        )
        assert repeated == quality

        with (tmp_path / "reports_final" / "lead_report.csv").open(
            encoding="utf-8", newline=""
        ) as stream:
            lead_report = list(csv.DictReader(stream))
        assert lead_report[0]["bucket"] == "completed"
        assert lead_report[0]["current_disposition"] == "renewed"
        assert lead_report[0]["phone_masked"] == "+91******3210"
    finally:
        connection.close()

