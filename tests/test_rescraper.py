"""Tests for rescrape_active_listings (price-change detection + deactivation)."""
from __future__ import annotations

from unittest.mock import MagicMock

import duckdb

from danang_realestate.pipeline.rescraper import rescrape_active_listings


def _conn_with_listings(rows):
    conn = duckdb.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE raw_listings (
            listing_id BIGINT, source VARCHAR, price BIGINT, price_per_sqm DOUBLE,
            is_active BOOLEAN, scraped_at TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE listing_price_history (
            listing_id BIGINT, source VARCHAR, price BIGINT, price_per_sqm DOUBLE,
            observed_at TIMESTAMP, previous_price BIGINT, price_change BIGINT,
            price_change_pct DOUBLE
        )
        """
    )
    for lid, price in rows:
        conn.execute(
            "INSERT INTO raw_listings VALUES (?, 'nhatot', ?, NULL, TRUE, NULL)", [lid, price]
        )
    return conn


def test_rescrape_deactivates_vanished_listing():
    conn = _conn_with_listings([(1, 2_000_000_000)])
    scraper = MagicMock()
    scraper.fetch_detail.return_value = None  # offline

    result = rescrape_active_listings(conn, scraper)

    assert result.checked == 1
    assert result.deactivated == 1
    assert result.price_updated == 0
    active = conn.execute("SELECT is_active FROM raw_listings WHERE listing_id = 1").fetchone()[0]
    assert active is False


def test_rescrape_records_price_change():
    conn = _conn_with_listings([(2, 2_000_000_000)])
    scraper = MagicMock()
    scraper.fetch_detail.return_value = {"price": 1_800_000_000, "size": 90}

    result = rescrape_active_listings(conn, scraper)

    assert result.price_updated == 1
    assert result.deactivated == 0
    new_price = conn.execute("SELECT price FROM raw_listings WHERE listing_id = 2").fetchone()[0]
    assert new_price == 1_800_000_000
    hist = conn.execute(
        "SELECT previous_price, price, price_change, ROUND(price_change_pct, 3) "
        "FROM listing_price_history WHERE listing_id = 2"
    ).fetchone()
    assert hist == (2_000_000_000, 1_800_000_000, -200_000_000, -0.1)


def test_rescrape_unchanged_price_no_history():
    conn = _conn_with_listings([(3, 2_000_000_000)])
    scraper = MagicMock()
    scraper.fetch_detail.return_value = {"price": 2_000_000_000, "size": 100}

    result = rescrape_active_listings(conn, scraper)

    assert result.price_updated == 0
    assert result.deactivated == 0
    n_hist = conn.execute(
        "SELECT count(*) FROM listing_price_history WHERE listing_id = 3"
    ).fetchone()[0]
    assert n_hist == 0


def test_rescrape_no_active_listings():
    conn = _conn_with_listings([])
    result = rescrape_active_listings(conn, MagicMock())
    assert result.checked == 0
