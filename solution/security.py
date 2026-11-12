"""PII tokenisation helpers. Raw PII is never persisted by this pipeline."""
from __future__ import annotations

import hashlib
import hmac
import os


def load_hmac_key(value: str | None = None) -> bytes:
    secret = value if value is not None else os.environ.get("PII_HMAC_KEY")
    if not secret:
        raise ValueError(
            "Set PII_HMAC_KEY to a stable random secret before ingesting data. "
            "Use the same key for every drop written to the same database."
        )
    encoded = secret.encode("utf-8")
    if len(encoded) < 16:
        raise ValueError("PII_HMAC_KEY must be at least 16 bytes.")
    return encoded


def pii_token(value: str, key: bytes, purpose: str) -> str:
    message = f"assignment-a:v1:{purpose}:{value}".encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()

