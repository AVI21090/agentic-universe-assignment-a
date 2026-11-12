"""Command-line entry point for Assignment A."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Sequence

from .config import load_dispositions, load_schemas
from .database import connect
from .ingest import ingest_drop
from .reports import write_reports
from .security import load_hmac_key


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m solution",
        description="Ingest lead/event drops and generate Assignment A reports.",
    )
    parser.add_argument("--db", dest="global_db", help="SQLite database path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="ingest one or more drop folders")
    ingest_parser.add_argument("--db", dest="command_db", help="SQLite database path")
    ingest_parser.add_argument("drops", nargs="+", help="drop directories, in arrival order")

    report_parser = subparsers.add_parser("report", help="write the five report files")
    report_parser.add_argument("--db", dest="command_db", help="SQLite database path")
    report_parser.add_argument("--out", required=True, help="directory for report files")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    db_path = (
        args.command_db
        or args.global_db
        or os.environ.get("LEDGER_DB")
        or "data/ledger.sqlite"
    )

    try:
        if args.command == "ingest":
            hmac_key = load_hmac_key()
            schemas = load_schemas()
            dispositions = load_dispositions()
            with closing(connect(db_path)) as connection:
                results = []
                for drop in args.drops:
                    results.append(ingest_drop(connection, drop, schemas, hmac_key))
            print(json.dumps({"database": db_path, "ingestion": results}, indent=2))
            return 0

        schemas = load_schemas()
        dispositions = load_dispositions()
        with closing(connect(db_path)) as connection:
            quality = write_reports(connection, args.out, schemas, dispositions)
        print(f"Wrote five reports to {Path(args.out)}")
        print(json.dumps(quality, indent=2))
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

