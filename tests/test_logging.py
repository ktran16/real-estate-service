"""Tests for structured (JSON) logging."""
from __future__ import annotations

import json
import logging

from danang_realestate.utils.logging import (
    JsonLogFormatter,
    configure_logging,
)


def _format(record: logging.LogRecord) -> dict:
    return json.loads(JsonLogFormatter().format(record))


def test_json_formatter_basic_fields():
    record = logging.LogRecord(
        name="danang", level=logging.INFO, pathname=__file__, lineno=1,
        msg="loaded %d listings", args=(42,), exc_info=None,
    )
    payload = _format(record)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "danang"
    assert payload["msg"] == "loaded 42 listings"
    assert "ts" in payload


def test_json_formatter_includes_extra_fields():
    record = logging.LogRecord(
        name="danang", level=logging.WARNING, pathname=__file__, lineno=1,
        msg="drop", args=(), exc_info=None,
    )
    record.mart = "listings"  # an `extra=` field
    record.drop_pct = 0.4
    payload = _format(record)
    assert payload["mart"] == "listings"
    assert payload["drop_pct"] == 0.4


def test_json_formatter_includes_exception():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="danang", level=logging.ERROR, pathname=__file__, lineno=1,
            msg="failed", args=(), exc_info=sys.exc_info(),
        )
    payload = _format(record)
    assert "ValueError: boom" in payload["exc"]


def test_configure_logging_json_mode(capsys):
    configure_logging(json_logs=True)
    try:
        logging.getLogger("danang_realestate.test").info("hello json")
        err = capsys.readouterr().err
        line = [ln for ln in err.splitlines() if "hello json" in ln][-1]
        payload = json.loads(line)
        assert payload["msg"] == "hello json"
    finally:
        # Restore pretty handler so JSON config doesn't leak into other tests.
        configure_logging(json_logs=False)


def test_configure_logging_is_idempotent():
    handler_a = configure_logging(json_logs=True)
    handler_b = configure_logging(json_logs=True)
    root = logging.getLogger()
    managed = [h for h in root.handlers if getattr(h, "_danang_managed", False)]
    assert len(managed) == 1
    assert handler_a not in root.handlers
    assert handler_b in root.handlers
    configure_logging(json_logs=False)
