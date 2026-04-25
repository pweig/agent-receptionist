"""
PII redaction for log output.

`redact(text)` scrubs common German PII patterns from free-form strings before
they are written to events.jsonl or printed at non-debug log levels.

Patterns redacted:
  - Dates in DD.MM.YYYY / DD/MM/YYYY / YYYY-MM-DD → [DOB]
  - German mobile (015x/016x/017x) and landline (0xx / +49xx) → [PHONE]

Free-form names are NOT redacted (too error-prone for German dental vocabulary).
Instead, STT transcription lines are suppressed entirely in production mode via
the LOG_PII env var — see DebugFrameLogger in main.py.
"""

from __future__ import annotations

import os
import re

# Compile once at import time.

_DATE_PATTERNS = re.compile(
    r"\b\d{1,2}[./]\d{1,2}[./]\d{4}\b"   # DD.MM.YYYY or DD/MM/YYYY
    r"|\b\d{4}-\d{2}-\d{2}\b"             # YYYY-MM-DD (ISO)
)

_PHONE_PATTERNS = re.compile(
    r"(?:\+49|0049)[\s\-./]?\d[\d\s\-./]{6,14}\d"   # +49 / 0049 international
    r"|0[1-9]\d[\s\-./]?\d[\d\s\-./]{4,12}\d"        # German landline / mobile
)


def redact(text: str) -> str:
    """Replace PII patterns in *text* with redaction tokens."""
    text = _DATE_PATTERNS.sub("[DOB]", text)
    text = _PHONE_PATTERNS.sub("[PHONE]", text)
    return text


def log_pii_enabled() -> bool:
    """Return True when raw PII logging is explicitly opted in (LOG_PII=true)."""
    return os.environ.get("LOG_PII", "false").lower() in ("1", "true", "yes")
