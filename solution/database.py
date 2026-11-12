"""SQLite schema and connection helpers."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    phone_token TEXT NOT NULL,
    phone_last4 TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    fields_json TEXT NOT NULL,
    pii_hashes_json TEXT NOT NULL,
    UNIQUE (workflow_id, phone_token)
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    call_id TEXT,
    occurred_at_us INTEGER,
    phone_token TEXT,
    disposition_code TEXT,
    source_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rejections (
    id INTEGER PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    row_number INTEGER NOT NULL,
    reason TEXT NOT NULL,
    source_path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processed_files (
    source_path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    kind TEXT NOT NULL,
    PRIMARY KEY (source_path, content_sha256)
);

CREATE TABLE IF NOT EXISTS quality_counters (
    name TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_leads_workflow_batch
    ON leads (workflow_id, batch_id);
CREATE INDEX IF NOT EXISTS idx_events_workflow_call
    ON events (workflow_id, call_id);
CREATE INDEX IF NOT EXISTS idx_events_workflow_phone
    ON events (workflow_id, phone_token);

CREATE TRIGGER IF NOT EXISTS events_are_append_only_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events is an append-only ledger');
END;

CREATE TRIGGER IF NOT EXISTS events_are_append_only_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events is an append-only ledger');
END;
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path_text = str(db_path)
    if path_text != ":memory:":
        Path(path_text).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path_text, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.executescript(SCHEMA_SQL)
    return connection


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[None]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()


def increment_counter(connection: sqlite3.Connection, name: str, amount: int) -> None:
    if amount <= 0:
        return
    connection.execute(
        """
        INSERT INTO quality_counters(name, value) VALUES (?, ?)
        ON CONFLICT(name) DO UPDATE SET value = value + excluded.value
        """,
        (name, amount),
    )

