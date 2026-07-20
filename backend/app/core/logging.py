"""Structured JSON logging with request correlation.

Two deliberate design choices:

1. **Redaction lives in the formatter.** Sensitive keys are scrubbed centrally,
   on the way out. The alternative — trusting every call site to remember not to
   log a password — fails exactly once and leaves credentials in a log
   aggregator forever. See OWASP A09.

2. **Correlation IDs use `contextvars`, not parameters.** A ContextVar survives
   `await` boundaries and is naturally per-task, so every log line emitted while
   handling a request carries its ID without threading an argument through every
   layer.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)
org_id_var: ContextVar[str | None] = ContextVar("org_id", default=None)

# Substring match, case-insensitive. Deliberately broad: a false positive costs
# one redacted debug line, a false negative costs a credential leak.
SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "cookie",
    "csrf",
    "api_key",
    "apikey",
    "access_key",
    "private",
    "ssn",
    "credit_card",
    "cvv",
)

REDACTED = "[REDACTED]"

# Attributes present on every LogRecord; anything else the caller attached via
# `extra=` is treated as structured context and included in the payload.
_STANDARD_ATTRS = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "module", "msecs",
        "message", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName", "taskName",
    }
)


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def redact(value: Any, _depth: int = 0) -> Any:
    """Recursively redact sensitive keys. Depth-capped against cyclic input."""
    if _depth > 6:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        return {
            key: REDACTED if _is_sensitive(str(key)) else redact(val, _depth + 1)
            for key, val in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item, _depth + 1) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    """One JSON object per line — parseable by any log aggregator."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if (request_id := request_id_var.get()) is not None:
            payload["request_id"] = request_id
        if (user_id := user_id_var.get()) is not None:
            payload["user_id"] = user_id
        if (org_id := org_id_var.get()) is not None:
            payload["organization_id"] = org_id

        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_ATTRS and not key.startswith("_")
        }
        if extras:
            payload["context"] = redact(extras)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        payload["source"] = f"{record.module}:{record.funcName}:{record.lineno}"

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    """Install the root handler. Idempotent — safe to call from lifespan."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter()
        if json_output
        else logging.Formatter("%(levelname)-8s %(name)s: %(message)s")
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Uvicorn ships its own handlers; drop them so everything is JSON and
    # correlated rather than two competing formats on one stream.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    # SQLAlchemy at INFO logs every statement, including bound parameters.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
