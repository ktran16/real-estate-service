"""Tests for detect_price_changes / get_existing_prices (price-change classification)."""
from __future__ import annotations

from datetime import datetime

import duckdb

from danang_realestate.models import NormalizedListing
from danang_realestate.pipeline.price_tracker import detect_price_changes, get_existing_prices


def _conn(existing: list[tuple[int, int | None]]) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    conn.execute(
        "CREATE TABLE raw_listings (listing_id BIGINT, source VARCHAR, price BIGINT)"
    )
    for lid, price in existing:
        conn.execute("INSERT INTO raw_listings VALUES (?, 'nhatot', ?)", [lid, price])
    return conn


def _listing(listing_id: int, price: int | None) -> NormalizedListing:
    return NormalizedListing(
        listing_id=listing_id,
        source="nhatot",
        url=f"https://example.com/{listing_id}",
        transaction_type="sale",
        price=price,
        scraped_at=datetime(2026, 5, 2),
        raw_json="{}",
    )


def test_empty_input_returns_empty():
    conn = _conn([])
    assert detect_price_changes(conn, []) == ([], [])


def test_new_listing_is_new_observation():
    conn = _conn([])
    new_obs, updates = detect_price_changes(conn, [_listing(1, 2_000_000_000)])
    assert len(new_obs) == 1
    assert updates == []
    assert new_obs[0].previous_price is None


def test_price_increase_is_update_with_pct():
    conn = _conn([(1, 2_000_000_000)])
    new_obs, updates = detect_price_changes(conn, [_listing(1, 2_200_000_000)])
    assert new_obs == []
    assert len(updates) == 1
    rec = updates[0]
    assert rec.previous_price == 2_000_000_000
    assert rec.price_change == 200_000_000
    assert abs(rec.price_change_pct - 0.10) < 1e-9


def test_unchanged_price_no_record():
    conn = _conn([(1, 2_000_000_000)])
    new_obs, updates = detect_price_changes(conn, [_listing(1, 2_000_000_000)])
    assert new_obs == []
    assert updates == []


def test_price_change_from_null_previous_has_no_pct():
    conn = _conn([(1, None)])
    _, updates = detect_price_changes(conn, [_listing(1, 2_000_000_000)])
    assert len(updates) == 1
    assert updates[0].previous_price is None
    assert updates[0].price_change is None
    assert updates[0].price_change_pct is None


def test_get_existing_prices_bulk():
    conn = _conn([(1, 100), (2, 200), (3, None)])
    prices = get_existing_prices(conn, [1, 2, 3, 4], "nhatot")
    assert prices == {1: 100, 2: 200, 3: None}  # id 4 absent


def test_get_existing_prices_empty_ids():
    conn = _conn([(1, 100)])
    assert get_existing_prices(conn, [], "nhatot") == {}
