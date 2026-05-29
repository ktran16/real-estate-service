from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return the current UTC time as a tz-naive datetime.

    Replaces the deprecated ``datetime.utcnow()`` while preserving naive-UTC
    semantics, matching the tz-naive TIMESTAMP columns used in DuckDB.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
