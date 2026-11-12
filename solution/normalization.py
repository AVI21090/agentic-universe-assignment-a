"""Pure normalisation helpers shared by lead and event ingestion."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


_HEADER_SEPARATORS = re.compile(r"[_\s]+")
_NON_DIGITS = re.compile(r"\D")
_CURRENCY_PREFIX = re.compile(r"^(?:₹|rs\.?|inr)\s*", re.IGNORECASE)


def normalize_header(value: Any) -> str:
    return _HEADER_SEPARATORS.sub(" ", str(value or "").strip().lower())


def normalize_phone(value: Any) -> str | None:
    """Return the canonical Indian mobile number, or None when it is invalid."""
    if value is None:
        return None
    digits = _NON_DIGITS.sub("", str(value))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] not in "6789":
        return None
    return "+91" + digits


def parse_date(value: str) -> str | None:
    """Parse only the four date formats specified in the assignment."""
    text = value.strip()
    formats = (
        (r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"),
        (r"\d{2}/\d{2}/\d{4}", "%d/%m/%Y"),
        (r"\d{2}-\d{2}-\d{4}", "%d-%m-%Y"),
        (r"\d{2}-[A-Za-z]{3}-\d{4}", "%d-%b-%Y"),
    )
    for pattern, fmt in formats:
        if re.fullmatch(pattern, text) is None:
            continue
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_decimal(value: str) -> str | None:
    """Parse a non-negative currency/decimal value and return two decimal places."""
    text = _CURRENCY_PREFIX.sub("", value.strip())
    text = text.replace(",", "")
    try:
        amount = Decimal(text)
        if not amount.is_finite() or amount < 0:
            return None
        rounded = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None
    return format(rounded, "f")


def parse_field_value(raw_value: Any, field: dict[str, Any]) -> tuple[Any | None, bool]:
    """Return (normalised value, invalid_nonempty_value). Empty values are missing."""
    text = "" if raw_value is None else str(raw_value).strip()
    if not text:
        return None, False

    field_type = field["type"]
    if field_type == "string":
        return text, False
    if field_type == "phone":
        value = normalize_phone(text)
        return value, value is None
    if field_type == "date":
        value = parse_date(text)
        return value, value is None
    if field_type == "decimal":
        value = parse_decimal(text)
        return value, value is None
    if field_type == "enum":
        value = text.lower()
        allowed = {str(item).lower() for item in field.get("values", [])}
        return (value, False) if value in allowed else (None, True)
    return None, True


def parse_event_time(value: Any) -> int | None:
    """Convert UTC, offset, or naive-as-IST ISO timestamps to UTC epoch microseconds."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
    utc_value = parsed.astimezone(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = utc_value - epoch
    return ((delta.days * 86400 + delta.seconds) * 1_000_000) + delta.microseconds

