"""Detect newly-found under-market deals and track which ones have been alerted.

The `deals` dbt mart lists active listings priced materially below their comparable benchmark
(rebuilt every run). This module diffs that mart against a `deal_alerts` tracking table so the
notification sensor fires once per deal rather than re-announcing the same listing every run.
"""
from __future__ import annotations

import logging

import duckdb

from danang_realestate.utils.timeutil import utcnow

logger = logging.getLogger(__name__)

_CREATE_SQL = """
    CREATE TABLE IF NOT EXISTS deal_alerts (
        listing_id    BIGINT    NOT NULL,
        source        VARCHAR   NOT NULL,
        discount_pct  DOUBLE,
        alerted_at    TIMESTAMP NOT NULL,
        PRIMARY KEY (listing_id, source)
    )
"""

_DEAL_COLUMNS = [
    "listing_id", "source", "title", "district", "ward", "price",
    "price_per_sqm", "benchmark_price_per_sqm", "discount_pct", "url",
]


def ensure_alerts_table(conn: duckdb.DuckDBPyConnection) -> None:
    """Create the deal_alerts tracking table if it doesn't already exist."""
    conn.execute(_CREATE_SQL)


def detect_new_deals(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    """Return deals in the `deals` mart that haven't been alerted yet (highest discount first).

    Returns ``[]`` when the `deals` mart doesn't exist (e.g. dbt hasn't run).
    """
    present = conn.execute(
        "SELECT count(*) FROM duckdb_tables() "
        "WHERE database_name = current_database() AND table_name = 'deals'"
    ).fetchone()
    if not present or not present[0]:
        return []
    ensure_alerts_table(conn)
    rows = conn.execute(
        """
        SELECT d.listing_id, d.source, d.title, d.district, d.ward, d.price,
               d.price_per_sqm, d.benchmark_price_per_sqm, d.discount_pct, d.url
        FROM deals d
        LEFT JOIN deal_alerts a
            ON d.listing_id = a.listing_id AND d.source = a.source
        WHERE a.listing_id IS NULL
        ORDER BY d.discount_pct DESC
        """
    ).fetchall()
    return [dict(zip(_DEAL_COLUMNS, row)) for row in rows]


def record_deal_alerts(conn: duckdb.DuckDBPyConnection, deals: list[dict], alerted_at=None) -> None:
    """Mark the given deals as alerted so they aren't re-announced on the next run."""
    if not deals:
        return
    ensure_alerts_table(conn)
    ts = alerted_at or utcnow()
    conn.executemany(
        "INSERT OR REPLACE INTO deal_alerts (listing_id, source, discount_pct, alerted_at) "
        "VALUES (?, ?, ?, ?)",
        [(d["listing_id"], d["source"], d.get("discount_pct"), ts) for d in deals],
    )
