"""Structured JSON logging with request correlation and secret redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

from nimbrix_api.core.request_context import get_request_id

# Credentials embedded in URLs (scheme://user:password@host) and common key=value secrets.
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<user>[^:/@\s]+):[^@\s]+@")
_KEY_VALUE_SECRET = re.compile(
    r"(?i)(?P<key>password|passwd|secret|token|api[_-]?key|access[_-]?key|authorization)"
    r"(?P<sep>\s*[=:]\s*)(?P<value>[^\s,;&]+)"
)
_STANDARD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    "color_message",  # uvicorn's ANSI-colored duplicate of the message
}


def redact(text: str) -> str:
    text = _URL_CREDENTIALS.sub(r"\g<scheme>\g<user>:***@", text)
    return _KEY_VALUE_SECRET.sub(r"\g<key>\g<sep>***", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        request_id = get_request_id()
        if request_id:
            payload["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = redact(value) if isinstance(value, str) else value
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Uvicorn's access log duplicates our request logging and bypasses redaction.
    logging.getLogger("uvicorn.access").handlers[:] = []
    logging.getLogger("uvicorn.access").propagate = False
    # Readiness probes inspect the Alembic version table; its INFO chatter is noise.
    logging.getLogger("alembic.runtime.migration").setLevel(logging.WARNING)
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers[:] = []
        logging.getLogger(name).propagate = True
