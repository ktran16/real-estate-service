"""Tests for deal detection + alert tracking (detect_new_deals / record_deal_alerts)."""
from __future__ import annotations

import duckdb

from danang_realestate.pipeline.deals import (
    detect_new_deals,
    ensure_alerts_table,
    record_deal_alerts,
)


def _conn_with_deals(rows) -> duckdb.DuckDBPyConnection:
    """rows: list of (listing_id, discount_pct). Builds a minimal `deals` mart."""
    conn = duckdb.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE deals (
            listing_id BIGINT, source VARCHAR, title VARCHAR, url VARCHAR,
            district VARCHAR, ward VARCHAR, price BIGINT, price_per_sqm DOUBLE,
            benchmark_price_per_sqm DOUBLE, comparable_count BIGINT, discount_pct DOUBLE
        )
        """
    )
    for lid, disc in rows:
        conn.execute(
            "INSERT INTO deals VALUES (?, 'nhatot', 'T', 'u', 'Hải Châu', 'W', "
            "2000000000, 30000000, 42000000, 4, ?)",
            [lid, disc],
        )
    return conn


def test_no_deals_table_returns_empty():
    conn = duckdb.connect(":memory:")
    assert detect_new_deals(conn) == []


def test_detects_all_deals_when_none_alerted():
    conn = _conn_with_deals([(1, 0.30), (2, 0.25)])
    new = detect_new_deals(conn)
    assert {d["listing_id"] for d in new} == {1, 2}
    # Highest discount first.
    assert [d["listing_id"] for d in new] == [1, 2]


def test_recorded_deals_are_not_redetected():
    conn = _conn_with_deals([(1, 0.30), (2, 0.25)])
    first = detect_new_deals(conn)
    record_deal_alerts(conn, first)
    assert detect_new_deals(conn) == []


def test_only_new_deals_detected_after_partial_alert():
    conn = _conn_with_deals([(1, 0.30)])
    record_deal_alerts(conn, detect_new_deals(conn))
    # A second, deeper deal appears on a later run.
    conn.execute(
        "INSERT INTO deals VALUES (3, 'nhatot', 'T', 'u', 'Hải Châu', 'W', "
        "1000000000, 20000000, 42000000, 4, 0.52)"
    )
    new = detect_new_deals(conn)
    assert [d["listing_id"] for d in new] == [3]


def test_record_is_idempotent():
    conn = _conn_with_deals([(1, 0.30)])
    deals = detect_new_deals(conn)
    record_deal_alerts(conn, deals)
    record_deal_alerts(conn, deals)  # INSERT OR REPLACE — no PK violation
    n = conn.execute("SELECT count(*) FROM deal_alerts").fetchone()[0]
    assert n == 1


def test_record_empty_is_noop():
    conn = duckdb.connect(":memory:")
    record_deal_alerts(conn, [])  # must not raise / create rows
    ensure_alerts_table(conn)
    assert conn.execute("SELECT count(*) FROM deal_alerts").fetchone()[0] == 0


def test_deal_payload_fields():
    conn = _conn_with_deals([(1, 0.30)])
    deal = detect_new_deals(conn)[0]
    for key in ("listing_id", "district", "ward", "price", "discount_pct", "url"):
        assert key in deal
