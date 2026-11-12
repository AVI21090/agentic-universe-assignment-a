"""Derive the five contractual reports from the stored leads and event ledger."""
from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .logic import latest_disposition, lead_bucket


IST = timezone(timedelta(hours=5, minutes=30))
UTC_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
BUCKETS = (
    "yet_to_reach_out",
    "in_progress_telephony_failed",
    "in_progress_dialed",
    "completed",
)


@dataclass
class CallState:
    workflow_id: str
    call_id: str
    lead_id: int
    initiated_seen: bool = False
    initiated_at_us: int | None = None
    initiated_event_id: str = ""
    connected: bool = False
    telephony_failed: bool = False
    latest_set: tuple[int | None, str, str] | None = None
    latest_override: tuple[int | None, str, str] | None = None

    @property
    def effective_disposition(self) -> str | None:
        sets = [] if self.latest_set is None else [self.latest_set]
        overrides = [] if self.latest_override is None else [self.latest_override]
        return latest_disposition(sets, overrides)


def _event_time_key(timestamp: int | None, event_id: str) -> tuple[int, str]:
    return (-1 if timestamp is None else timestamp, event_id)


def _consider_initiated(state: CallState, timestamp: int | None, event_id: str) -> None:
    if not state.initiated_seen:
        state.initiated_seen = True
        state.initiated_at_us = timestamp
        state.initiated_event_id = event_id
        return
    current = state.initiated_at_us
    if current is None and timestamp is not None:
        state.initiated_at_us = timestamp
        state.initiated_event_id = event_id
    elif timestamp is not None and current is not None and timestamp < current:
        state.initiated_at_us = timestamp
        state.initiated_event_id = event_id
    elif timestamp == current and event_id < state.initiated_event_id:
        state.initiated_event_id = event_id


def _to_datetime(timestamp_us: int) -> datetime:
    return UTC_EPOCH + timedelta(microseconds=timestamp_us)


def _write_csv(path: Path, header: list[str], rows: list[list[Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def _quality_report(
    connection: sqlite3.Connection,
    orphan_events: int,
    unknown_disposition_events: int,
) -> dict[str, Any]:
    counters = {
        row["name"]: row["value"]
        for row in connection.execute("SELECT name, value FROM quality_counters")
    }
    reasons = {
        row["reason"]: row["count"]
        for row in connection.execute(
            """
            SELECT reason, COUNT(*) AS count
            FROM rejections
            GROUP BY reason
            ORDER BY reason
            """
        )
    }
    rejected = sum(reasons.values())
    return {
        "lead_rows_total": counters.get("lead_rows_total", 0),
        "lead_rows_rejected": rejected,
        "rejections_by_reason": reasons,
        "duplicate_lead_rows_merged": counters.get("duplicate_lead_rows_merged", 0),
        "field_warnings": counters.get("field_warnings", 0),
        "events_total": counters.get("events_total", 0),
        "malformed_events": counters.get("malformed_events", 0),
        "duplicate_events_dropped": counters.get("duplicate_events_dropped", 0),
        "orphan_events": orphan_events,
        "unknown_disposition_events": unknown_disposition_events,
    }


def write_reports(
    connection: sqlite3.Connection,
    out_dir: str | Path,
    schemas: dict[str, dict[str, Any]],
    dispositions: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)

    lead_rows = list(connection.execute("SELECT * FROM leads"))
    lead_lookup = {
        (row["workflow_id"], row["phone_token"]): row["id"]
        for row in lead_rows
    }

    # Overrides have no phone. Resolve their call to the deterministic lead linked
    # by any other phone-bearing event for that workflow/call pair.
    call_to_lead = {
        (row["workflow_id"], row["call_id"]): row["lead_id"]
        for row in connection.execute(
            """
            SELECT e.workflow_id, e.call_id, MIN(l.id) AS lead_id
            FROM events AS e
            JOIN leads AS l
              ON l.workflow_id = e.workflow_id
             AND l.phone_token = e.phone_token
            WHERE e.call_id IS NOT NULL
              AND e.event_type != 'disposition.override'
            GROUP BY e.workflow_id, e.call_id
            """
        )
    }

    calls: dict[tuple[str, str, int], CallState] = {}
    orphan_events = 0
    unknown_disposition_events = 0

    for event in connection.execute(
        """
        SELECT event_id, event_type, workflow_id, call_id, occurred_at_us,
               phone_token, disposition_code
        FROM events
        """
    ):
        workflow_id = event["workflow_id"]
        event_type = event["event_type"]
        call_id = event["call_id"]
        if event_type == "disposition.override":
            lead_id = call_to_lead.get((workflow_id, call_id))
        else:
            lead_id = lead_lookup.get((workflow_id, event["phone_token"]))

        if lead_id is None:
            orphan_events += 1
            continue

        code = event["disposition_code"]
        known_codes = dispositions.get(workflow_id, {})
        is_disposition_event = event_type.startswith("disposition.")
        is_known_disposition = code is not None and code in known_codes
        if is_disposition_event and not is_known_disposition:
            unknown_disposition_events += 1

        if not call_id:
            continue
        state_key = (workflow_id, call_id, lead_id)
        state = calls.get(state_key)
        if state is None:
            state = CallState(workflow_id=workflow_id, call_id=call_id, lead_id=lead_id)
            calls[state_key] = state

        timestamp = event["occurred_at_us"]
        event_id = event["event_id"]
        if event_type == "call.initiated":
            _consider_initiated(state, timestamp, event_id)
        elif event_type == "call.connected":
            state.connected = True
        elif event_type == "call.telephony_failed":
            state.telephony_failed = True
        elif event_type == "disposition.set" and is_known_disposition:
            candidate = (timestamp, event_id, code)
            if state.latest_set is None or _event_time_key(*candidate[:2]) > _event_time_key(*state.latest_set[:2]):
                state.latest_set = candidate
        elif event_type == "disposition.override" and is_known_disposition:
            candidate = (timestamp, event_id, code)
            if state.latest_override is None or _event_time_key(*candidate[:2]) > _event_time_key(*state.latest_override[:2]):
                state.latest_override = candidate

    initiated_calls = [call for call in calls.values() if call.initiated_seen]
    calls_by_lead: dict[int, list[CallState]] = defaultdict(list)
    daily: dict[tuple[str, str], dict[str, Any]] = {}
    for call in initiated_calls:
        calls_by_lead[call.lead_id].append(call)
        if call.initiated_at_us is None:
            continue
        initiated_datetime = _to_datetime(call.initiated_at_us).astimezone(IST)
        day = initiated_datetime.date().isoformat()
        daily_row = daily.setdefault(
            (call.workflow_id, day),
            {"total": 0, "connected": 0, "success_leads": set()},
        )
        daily_row["total"] += 1
        if call.connected:
            daily_row["connected"] += 1
        code = call.effective_disposition
        if code is not None and dispositions.get(call.workflow_id, {}).get(code, {}).get("is_success", False):
            daily_row["success_leads"].add(call.lead_id)

    lead_buckets: dict[int, str] = {}
    for lead in lead_rows:
        workflow_dispositions = dispositions.get(lead["workflow_id"], {})
        lead_buckets[lead["id"]] = lead_bucket(
            calls_by_lead.get(lead["id"], []), workflow_dispositions
        )

    # 1. Batch summary.
    batches: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for lead in lead_rows:
        key = (lead["workflow_id"], lead["batch_id"])
        batches[key]["total_leads"] += 1
        batches[key][lead_buckets[lead["id"]]] += 1
    batch_rows = []
    for (workflow_id, batch_id), counts in sorted(batches.items()):
        batch_rows.append([
            workflow_id,
            batch_id,
            counts["total_leads"],
            counts["yet_to_reach_out"],
            counts["in_progress_telephony_failed"],
            counts["in_progress_dialed"],
            counts["completed"],
        ])
    _write_csv(
        output / "batch_summary.csv",
        [
            "workflow_id", "batch_id", "total_leads", "yet_to_reach_out",
            "in_progress_telephony_failed", "in_progress_dialed", "completed",
        ],
        batch_rows,
    )

    # 2. Daily overview.
    daily_rows = []
    for (workflow_id, day), counts in sorted(daily.items()):
        total = counts["total"]
        rate = counts["connected"] / total if total else 0.0
        daily_rows.append([
            workflow_id,
            day,
            total,
            counts["connected"],
            f"{rate:.4f}",
            len(counts["success_leads"]),
        ])
    _write_csv(
        output / "daily_overview.csv",
        [
            "workflow_id", "date", "total_calls", "connected_calls",
            "connectivity_rate", "success_leads",
        ],
        daily_rows,
    )

    # 3. One row per lead.
    lead_report_rows = []
    for lead in sorted(
        lead_rows,
        key=lambda item: (
            item["workflow_id"],
            json.loads(item["fields_json"]).get(
                next(
                    field["name"]
                    for field in schemas[item["workflow_id"]]["fields"]
                    if field.get("reference")
                ),
                "",
            ) or "",
        ),
    ):
        values = json.loads(lead["fields_json"])
        reference_field = next(
            field["name"]
            for field in schemas[lead["workflow_id"]]["fields"]
            if field.get("reference")
        )
        lead_calls = calls_by_lead.get(lead["id"], [])
        valid_times = [
            call.initiated_at_us for call in lead_calls if call.initiated_at_us is not None
        ]
        last_call_at = ""
        if valid_times:
            last_call_at = _to_datetime(max(valid_times)).astimezone(IST).isoformat(timespec="seconds")

        calls_with_disposition = [
            call for call in lead_calls if call.effective_disposition is not None
        ]
        current_disposition = ""
        if calls_with_disposition:
            most_recent = max(
                calls_with_disposition,
                key=lambda call: (
                    -1 if call.initiated_at_us is None else call.initiated_at_us,
                    call.call_id,
                ),
            )
            current_disposition = most_recent.effective_disposition or ""

        ever_success = any(
            call.effective_disposition is not None
            and dispositions.get(lead["workflow_id"], {})
                .get(call.effective_disposition, {})
                .get("is_success", False)
            for call in lead_calls
        )
        lead_report_rows.append([
            lead["workflow_id"],
            values.get(reference_field) or "",
            lead["batch_id"],
            "+91******" + lead["phone_last4"],
            lead_buckets[lead["id"]],
            len(lead_calls),
            sum(1 for call in lead_calls if call.connected),
            last_call_at,
            current_disposition,
            "true" if ever_success else "false",
        ])
    _write_csv(
        output / "lead_report.csv",
        [
            "workflow_id", "external_ref", "batch_id", "phone_masked", "bucket",
            "total_calls", "connected_calls", "last_call_at", "current_disposition",
            "ever_success",
        ],
        lead_report_rows,
    )

    # 4. Rejections, deliberately containing no PII.
    rejection_rows = [
        [
            row["workflow_id"],
            row["batch_id"],
            row["row_number"],
            row["reason"],
        ]
        for row in connection.execute(
            """
            SELECT workflow_id, batch_id, row_number, reason
            FROM rejections
            ORDER BY workflow_id, batch_id, row_number, source_path, id
            """
        )
    ]
    _write_csv(
        output / "rejections.csv",
        ["workflow_id", "batch_id", "row_number", "reason"],
        rejection_rows,
    )

    # 5. Quality metrics. Orphans/unknown dispositions are derived now so an
    # earlier orphan can become linked when a later drop adds its lead.
    quality = _quality_report(connection, orphan_events, unknown_disposition_events)
    with (output / "data_quality.json").open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(quality, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    return quality

