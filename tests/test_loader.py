"""Tests for load_listings — upsert + price-history transaction path (incl. rollback)."""
from __future__ import annotations

from datetime import datetime

import pytest

from danang_realestate import db as db_module
from danang_realestate.models import NormalizedListing
from danang_realestate.pipeline.loader import load_listings


def _listing(listing_id: int, price: int | None, scraped_at: datetime) -> NormalizedListing:
    return NormalizedListing(
        listing_id=listing_id,
        source="nhatot",
        url=f"https://example.com/{listing_id}",
        transaction_type="sale",
        price=price,
        area_sqm=100.0,
        scraped_at=scraped_at,
        raw_json="{}",
    )


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    """A real DuckDB connection with the production schema (via init_db) on a temp file."""
    monkeypatch.setattr(db_module.settings, "duckdb_path", str(tmp_path / "test.duckdb"))
    db_module.init_db()
    connection = db_module.get_connection()
    yield connection
    connection.close()


def _count(conn, table) -> int:
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_empty_listings_is_noop(conn):
    load_listings(conn, [])
    assert _count(conn, "raw_listings") == 0
    assert _count(conn, "listing_price_history") == 0


def test_first_load_records_new_observations(conn):
    listings = [
        _listing(1, 2_000_000_000, datetime(2026, 5, 1)),
        _listing(2, 3_000_000_000, datetime(2026, 5, 1)),
    ]
    load_listings(conn, listings)

    assert _count(conn, "raw_listings") == 2
    # Each new listing gets a baseline price-history row with no previous_price.
    rows = conn.execute(
        "SELECT listing_id, price, previous_price, price_change "
        "FROM listing_price_history ORDER BY listing_id"
    ).fetchall()
    assert rows == [
        (1, 2_000_000_000, None, None),
        (2, 3_000_000_000, None, None),
    ]


def test_duplicate_listing_in_batch_is_deduped(conn):
    # The same ad can appear under multiple nhatot categories within one scrape: identical
    # (listing_id, source, scraped_at). Without deduping, the second new-observation row
    # collides on the price-history PK and rolls back the whole load. It must not.
    ts = datetime(2026, 5, 1)
    load_listings(conn, [_listing(1, 2_000_000_000, ts), _listing(1, 2_000_000_000, ts)])

    assert _count(conn, "raw_listings") == 1
    assert _count(conn, "listing_price_history") == 1


def test_price_change_records_history_and_updates_listing(conn):
    load_listings(conn, [_listing(1, 2_000_000_000, datetime(2026, 5, 1))])
    # Re-load the same listing at a lower price.
    load_listings(conn, [_listing(1, 1_800_000_000, datetime(2026, 5, 2))])

    # raw_listings reflects the new price (INSERT OR REPLACE on PK).
    assert conn.execute("SELECT price FROM raw_listings WHERE listing_id = 1").fetchone()[0] == (
        1_800_000_000
    )
    # A second history row captures the change with computed delta + pct.
    change = conn.execute(
        "SELECT previous_price, price, price_change, ROUND(price_change_pct, 4) "
        "FROM listing_price_history WHERE listing_id = 1 AND previous_price IS NOT NULL"
    ).fetchone()
    assert change == (2_000_000_000, 1_800_000_000, -200_000_000, -0.1)


def test_unchanged_price_adds_no_history(conn):
    load_listings(conn, [_listing(1, 2_000_000_000, datetime(2026, 5, 1))])
    load_listings(conn, [_listing(1, 2_000_000_000, datetime(2026, 5, 2))])
    # Only the original baseline row — no change row.
    assert _count(conn, "listing_price_history") == 1


def test_transaction_rolls_back_on_history_failure(conn):
    load_listings(conn, [_listing(1, 2_000_000_000, datetime(2026, 5, 1))])
    # Break the history insert: dropping the table makes the in-transaction INSERT fail, so the
    # whole load (including the raw_listings price update) must roll back.
    conn.execute("DROP TABLE listing_price_history")

    with pytest.raises(Exception):
        load_listings(conn, [_listing(1, 1_800_000_000, datetime(2026, 5, 2))])

    # The price update was rolled back — raw_listings still shows the original price.
    assert conn.execute("SELECT price FROM raw_listings WHERE listing_id = 1").fetchone()[0] == (
        2_000_000_000
    )
