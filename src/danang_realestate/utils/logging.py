"""Structured (JSON) logging support.

The pipeline runs unattended under Dagster, so machine-parseable logs make it far easier to
search/aggregate run output. `configure_logging()` installs either a JSON formatter (one
object per line) or the human-friendly Rich handler used during interactive CLI runs.

JSON mode is opt-in via the ``LOG_JSON`` env var (``1``/``true``/``yes``) so local CLI runs
keep their pretty output while containerized deployments can flip a single env var to get
structured logs.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

# Standard LogRecord attributes — anything *not* in here that a caller attaches via
# `logger.info(..., extra={...})` is treated as a structured field and merged into the JSON.
_RESERVED = set(
    logging.makeLogRecord({}).__dict__
) | {"message", "asctime", "taskName"}


class JsonLogFormatter(logging.Formatter):
    """Render a LogRecord as a single-line JSON object.

    Includes any ``extra=`` fields the caller attached, plus exception/stack info when present.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _json_enabled(explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    return os.getenv("LOG_JSON", "").strip().lower() in {"1", "true", "yes", "on"}


def configure_logging(
    *, json_logs: bool | None = None, level: int = logging.INFO
) -> logging.Handler:
    """Configure root logging and return the installed handler.

    With JSON disabled this falls back to Rich (pretty CLI output); with it enabled every log
    line is a JSON object on stderr. Idempotent: replaces any handlers a previous call added.
    """
    root = logging.getLogger()
    root.setLevel(level)
    # Drop handlers a previous configure_logging() installed so repeated calls don't double-log.
    for existing in list(root.handlers):
        if getattr(existing, "_danang_managed", False):
            root.removeHandler(existing)

    handler: logging.Handler
    if _json_enabled(json_logs):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
    else:
        from rich.logging import RichHandler

        handler = RichHandler(rich_tracebacks=True)
        handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))

    handler.setLevel(level)
    setattr(handler, "_danang_managed", True)
    root.addHandler(handler)
    return handler
