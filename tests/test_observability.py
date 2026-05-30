"""Tests for mart row-count history + drop detection."""
from __future__ import annotations

from datetime import datetime

import duckdb

from danang_realestate.pipeline.observability import (
    DEFAULT_DROP_THRESHOLD,
    detect_row_drops,
    record_mart_counts,
)


def _conn() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(":memory:")


def test_no_drops_before_two_snapshots():
    conn = _conn()
    # No table yet.
    assert detect_row_drops(conn) == []
    # One snapshot is still not comparable.
    record_mart_counts(conn, {"listings": 100}, run_at=datetime(2026, 5, 1))
    assert detect_row_drops(conn) == []


def test_detects_material_drop():
    conn = _conn()
    record_mart_counts(conn, {"listings": 100, "price_by_district": 6}, run_at=datetime(2026, 5, 1))
    record_mart_counts(conn, {"listings": 60, "price_by_district": 6}, run_at=datetime(2026, 5, 2))

    drops = detect_row_drops(conn)
    assert len(drops) == 1
    drop = drops[0]
    assert drop["mart"] == "listings"
    assert drop["previous"] == 100
    assert drop["current"] == 60
    assert abs(drop["drop_pct"] - 0.40) < 1e-9


def test_small_decline_below_threshold_ignored():
    conn = _conn()
    record_mart_counts(conn, {"listings": 100}, run_at=datetime(2026, 5, 1))
    # 10% decline — well under the 30% default.
    record_mart_counts(conn, {"listings": 90}, run_at=datetime(2026, 5, 2))
    assert detect_row_drops(conn) == []


def test_growth_is_not_a_drop():
    conn = _conn()
    record_mart_counts(conn, {"listings": 100}, run_at=datetime(2026, 5, 1))
    record_mart_counts(conn, {"listings": 150}, run_at=datetime(2026, 5, 2))
    assert detect_row_drops(conn) == []


def test_mart_vanishing_counts_as_full_drop():
    conn = _conn()
    record_mart_counts(conn, {"listings": 100}, run_at=datetime(2026, 5, 1))
    # Mart absent in the newer snapshot → treated as current=0 → 100% drop.
    record_mart_counts(conn, {"price_by_district": 6}, run_at=datetime(2026, 5, 2))
    drops = detect_row_drops(conn)
    assert len(drops) == 1
    assert drops[0]["mart"] == "listings"
    assert drops[0]["current"] == 0
    assert drops[0]["drop_pct"] == 1.0


def test_only_two_most_recent_runs_compared():
    conn = _conn()
    # A big historical count must not affect the comparison of the latest two runs.
    record_mart_counts(conn, {"listings": 1000}, run_at=datetime(2026, 5, 1))
    record_mart_counts(conn, {"listings": 100}, run_at=datetime(2026, 5, 2))
    record_mart_counts(conn, {"listings": 95}, run_at=datetime(2026, 5, 3))
    assert detect_row_drops(conn) == []


def test_custom_threshold():
    conn = _conn()
    record_mart_counts(conn, {"listings": 100}, run_at=datetime(2026, 5, 1))
    record_mart_counts(conn, {"listings": 85}, run_at=datetime(2026, 5, 2))
    assert detect_row_drops(conn) == []  # 15% < default 30%
    assert len(detect_row_drops(conn, threshold=0.10)) == 1


def test_default_threshold_value():
    assert 0 < DEFAULT_DROP_THRESHOLD < 1
