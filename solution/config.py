"""Assignment configuration loaded from the repository's supplied schemas."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "assignment-a-data" / "schemas"


def load_schemas() -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}
    for path in sorted(SCHEMA_DIR.glob("*.json")):
        with path.open(encoding="utf-8") as stream:
            schema = json.load(stream)
        schemas[schema["workflow_id"]] = schema
    return schemas


def load_dispositions() -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = {}
    path = SCHEMA_DIR / "dispositions.csv"
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            result.setdefault(row["workflow_id"], {})[row["code"]] = {
                "label": row["label"],
                "is_terminal": row["is_terminal"].strip().lower() == "true",
                "is_success": row["is_success"].strip().lower() == "true",
            }
    return result

