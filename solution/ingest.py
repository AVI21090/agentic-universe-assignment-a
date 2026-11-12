"""File ingestion for leads and the append-only interaction ledger."""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from .database import increment_counter, transaction
from .normalization import normalize_header, normalize_phone, parse_event_time, parse_field_value
from .security import pii_token


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _was_processed(connection: sqlite3.Connection, source_path: str, digest: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM processed_files WHERE source_path = ? AND content_sha256 = ?",
        (source_path, digest),
    ).fetchone() is not None


def _mark_processed(
    connection: sqlite3.Connection, source_path: str, digest: str, kind: str
) -> None:
    connection.execute(
        "INSERT INTO processed_files(source_path, content_sha256, kind) VALUES (?, ?, ?)",
        (source_path, digest, kind),
    )


def _build_header_map(headers: list[str], schema: dict[str, Any]) -> dict[str, list[int]]:
    aliases: dict[str, str] = {}
    for field in schema["fields"]:
        for candidate in [field["name"], *field.get("aliases", [])]:
            aliases[normalize_header(candidate)] = field["name"]

    mapped: dict[str, list[int]] = {}
    for index, header in enumerate(headers):
        field_name = aliases.get(normalize_header(header))
        if field_name is not None:
            mapped.setdefault(field_name, []).append(index)
    return mapped


def _raw_value(row: list[str], indices: list[int]) -> str:
    # If a file contains two headers for the same schema field, the first non-empty
    # value in header order wins. This makes duplicate aliases deterministic.
    for index in indices:
        if index < len(row):
            value = row[index]
            if value is not None and str(value).strip():
                return str(value)
    return ""


def _normalise_lead_row(
    row: list[str],
    header_map: dict[str, list[int]],
    schema: dict[str, Any],
) -> tuple[dict[str, Any], int, str | None]:
    values: dict[str, Any] = {}
    warnings = 0
    reason: str | None = None

    for field in schema["fields"]:
        name = field["name"]
        raw = _raw_value(row, header_map.get(name, []))
        value, invalid = parse_field_value(raw, field)
        if field["type"] == "phone" and raw.strip() and invalid:
            reason = "invalid_phone"
            break
        if field.get("required") and value is None:
            reason = f"missing_required:{name}"
            break
        if invalid:
            warnings += 1
        values[name] = value

    return values, warnings, reason


def _apply_defaults(values: dict[str, Any], schema: dict[str, Any]) -> None:
    for field in schema["fields"]:
        if field.get("default") is not None and values.get(field["name"]) is None:
            values[field["name"]] = field["default"]


def _ingest_lead_file(
    connection: sqlite3.Connection,
    path: Path,
    digest: str,
    workflow_id: str,
    schema: dict[str, Any],
    hmac_key: bytes,
) -> dict[str, int]:
    totals: Counter[str] = Counter()
    batch_id = path.stem
    source_path = str(path.resolve())

    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        try:
            headers = next(reader)
        except StopIteration:
            return {}
        header_map = _build_header_map(headers, schema)

        row_number = 0
        for row in reader:
            if not any(cell is not None and str(cell).strip() for cell in row):
                continue
            row_number += 1
            totals["lead_rows_total"] += 1
            values, warnings, reason = _normalise_lead_row(row, header_map, schema)

            if reason is not None:
                connection.execute(
                    """
                    INSERT INTO rejections(
                        workflow_id, batch_id, row_number, reason, source_path, content_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (workflow_id, batch_id, row_number, reason, source_path, digest),
                )
                continue

            totals["field_warnings"] += warnings
            canonical_phone = values["phone"]
            phone_token = pii_token(canonical_phone, hmac_key, "phone")
            phone_last4 = canonical_phone[-4:]

            pii_hashes: dict[str, str] = {}
            non_pii_values: dict[str, Any] = {}
            for field in schema["fields"]:
                name = field["name"]
                value = values.get(name)
                if field.get("pii"):
                    if name != "phone" and value is not None:
                        pii_hashes[name] = pii_token(str(value), hmac_key, name)
                elif name != "phone":
                    non_pii_values[name] = value

            existing = connection.execute(
                """
                SELECT id, fields_json, pii_hashes_json
                FROM leads
                WHERE workflow_id = ? AND phone_token = ?
                """,
                (workflow_id, phone_token),
            ).fetchone()

            if existing is None:
                merged_values = dict(non_pii_values)
                _apply_defaults(merged_values, schema)
                connection.execute(
                    """
                    INSERT INTO leads(
                        workflow_id, phone_token, phone_last4, batch_id,
                        fields_json, pii_hashes_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        workflow_id,
                        phone_token,
                        phone_last4,
                        batch_id,
                        json.dumps(merged_values, ensure_ascii=False, sort_keys=True),
                        json.dumps(pii_hashes, ensure_ascii=False, sort_keys=True),
                    ),
                )
            else:
                merged_values = json.loads(existing["fields_json"])
                for name, value in non_pii_values.items():
                    if value is not None:
                        merged_values[name] = value
                _apply_defaults(merged_values, schema)

                merged_pii = json.loads(existing["pii_hashes_json"])
                for name, value in pii_hashes.items():
                    if value is not None:
                        merged_pii[name] = value

                connection.execute(
                    """
                    UPDATE leads
                    SET fields_json = ?, pii_hashes_json = ?
                    WHERE id = ?
                    """,
                    (
                        json.dumps(merged_values, ensure_ascii=False, sort_keys=True),
                        json.dumps(merged_pii, ensure_ascii=False, sort_keys=True),
                        existing["id"],
                    ),
                )
                totals["duplicate_lead_rows_merged"] += 1

    for name, amount in totals.items():
        increment_counter(connection, name, amount)
    return dict(totals)


def _ingest_event_file(
    connection: sqlite3.Connection,
    path: Path,
    hmac_key: bytes,
) -> dict[str, int]:
    totals: Counter[str] = Counter()
    source_path = str(path.resolve())

    with path.open(encoding="utf-8-sig") as stream:
        for line in stream:
            line = line.rstrip("\r\n")
            if line == "":
                continue
            totals["events_total"] += 1
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                totals["malformed_events"] += 1
                continue

            if not isinstance(event, dict) or event.get("event_id") is None:
                totals["malformed_events"] += 1
                continue
            event_id = str(event["event_id"]).strip()
            if not event_id:
                totals["malformed_events"] += 1
                continue

            workflow_id = str(event.get("workflow_id") or "").strip()
            event_type = str(event.get("event_type") or "").strip()
            raw_call_id = event.get("call_id")
            call_id = None if raw_call_id is None else str(raw_call_id).strip() or None

            phone = normalize_phone(event.get("phone"))
            phone_token = pii_token(phone, hmac_key, "phone") if phone is not None else None
            payload = event.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            raw_code = payload.get("disposition_code")
            disposition_code = None if raw_code is None else str(raw_code).strip()
            occurred_at_us = parse_event_time(event.get("occurred_at"))

            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO events(
                    event_id, event_type, workflow_id, call_id, occurred_at_us,
                    phone_token, disposition_code, source_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    event_type,
                    workflow_id,
                    call_id,
                    occurred_at_us,
                    phone_token,
                    disposition_code,
                    source_path,
                ),
            )
            if cursor.rowcount == 0:
                totals["duplicate_events_dropped"] += 1

    for name, amount in totals.items():
        increment_counter(connection, name, amount)
    return dict(totals)


def ingest_drop(
    connection: sqlite3.Connection,
    drop_path: str | Path,
    schemas: dict[str, dict[str, Any]],
    hmac_key: bytes,
) -> dict[str, Any]:
    """Ingest one drop atomically. Re-ingesting unchanged files is a no-op."""
    drop = Path(drop_path)
    if not drop.is_dir():
        raise FileNotFoundError(f"Drop directory does not exist: {drop}")

    lead_files = sorted(
        drop.glob("leads/*/*.csv"),
        key=lambda item: (item.parent.name, item.name),
    )
    event_files = sorted(drop.glob("events/*.jsonl"), key=lambda item: item.name)
    processed = 0
    skipped = 0
    counters: Counter[str] = Counter()

    with transaction(connection):
        for path in lead_files:
            workflow_id = path.parent.name
            if workflow_id not in schemas:
                raise ValueError(f"No lead schema found for workflow: {workflow_id}")
            digest = _sha256_file(path)
            source_path = str(path.resolve())
            if _was_processed(connection, source_path, digest):
                skipped += 1
                continue
            result = _ingest_lead_file(
                connection, path, digest, workflow_id, schemas[workflow_id], hmac_key
            )
            counters.update(result)
            _mark_processed(connection, source_path, digest, "leads")
            processed += 1

        for path in event_files:
            digest = _sha256_file(path)
            source_path = str(path.resolve())
            if _was_processed(connection, source_path, digest):
                skipped += 1
                continue
            result = _ingest_event_file(connection, path, hmac_key)
            counters.update(result)
            _mark_processed(connection, source_path, digest, "events")
            processed += 1

    return {
        "drop": str(drop),
        "files_processed": processed,
        "files_skipped": skipped,
        "counters": dict(counters),
    }

