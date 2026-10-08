"""Structured logging: a small JSON formatter and the request context (CIS §19.1, D-55).

Records carry only whitelisted metadata. Exceptions are rendered as their type and stack frames
(file, line, function): never the exception message, which can contain input, and never local
variables. No configuration enables payload logging.
"""

import json
import logging
import sys
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

from backend.config import LogFormat, Settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Metadata that may be attached with `extra=` (§19.1). Anything else is dropped.
ALLOWED_FIELDS = (
    "review_id",
    "stage",
    "status",
    "duration_ms",
    "error_code",
    "replayed",
    "method",
    "path",
    "status_code",
)
_HANDLER_NAME = "ai-code-review-assistant"


@contextmanager
def request_context(request_id: str) -> Iterator[None]:
    """Bind the request ID for the duration of one request (and the tasks it starts)."""
    token = request_id_var.set(request_id)
    try:
        yield
    finally:
        request_id_var.reset(token)


def _fields(record: logging.LogRecord) -> dict[str, object]:
    fields: dict[str, object] = {
        "ts": datetime.fromtimestamp(record.created, UTC).isoformat().replace("+00:00", "Z"),
        "level": record.levelname,
        "logger": record.name,
        "event": record.getMessage(),
        "request_id": request_id_var.get(),
    }
    fields.update({k: getattr(record, k) for k in ALLOWED_FIELDS if hasattr(record, k)})
    if record.exc_info and record.exc_info[1] is not None:
        error = record.exc_info[1]
        fields["exception_type"] = type(error).__name__
        fields["traceback"] = [
            f"{Path(frame.filename).name}:{frame.lineno} in {frame.name}"
            for frame in traceback.extract_tb(error.__traceback__)
        ]
    return {k: v for k, v in fields.items() if v is not None}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(_fields(record), default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = _fields(record)
        head = (
            f"{fields.pop('ts')} {fields.pop('level')} {fields.pop('logger')} {fields.pop('event')}"
        )
        return " ".join([head, *(f"{k}={v}" for k, v in fields.items())])


def configure_logging(settings: Settings) -> None:
    """Install the formatter on the root logger once; other handlers are left in place."""
    root = logging.getLogger()
    root.setLevel(settings.log_level.value)
    formatter = JsonFormatter() if settings.log_format is LogFormat.JSON else ConsoleFormatter()
    for handler in root.handlers:
        if handler.get_name() == _HANDLER_NAME:
            handler.setFormatter(formatter)
            return
    handler = logging.StreamHandler(sys.stderr)
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(formatter)
    root.addHandler(handler)
