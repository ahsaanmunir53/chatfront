"""Logging that never writes phone numbers, emails or tokens."""

from __future__ import annotations

import logging
import re

_BEARER = re.compile(r"(?i)(bearer\s+)[\w.-]+")
_PII = (
    re.compile(r"\+\d[\d\s-]{8,}\d"),  # international numbers written with +
    re.compile(r"(?<!\d)0\d{3}[\s-]?\d{7}(?!\d)"),  # Pakistani local mobile numbers
    re.compile(r"(?<![\d.])\d{11,15}(?![\d.])"),  # bare long digit runs (numbers, TIDs)
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),  # emails
)


def redact(text: str) -> str:
    text = _BEARER.sub(r"\1[redacted]", text)
    for pattern in _PII:
        text = pattern.sub("[redacted]", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def setup_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "fontTools"):  # fontTools: a line per PDF glyph
        logging.getLogger(noisy).setLevel(logging.WARNING)
