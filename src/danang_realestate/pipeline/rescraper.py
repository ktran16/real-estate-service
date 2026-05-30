"""Re-check active listings: detect price changes and mark vanished listings inactive.

Extracted from the `rescrape` CLI command so it can also run as a Dagster op (the daily
search-results scrape catches price changes for still-listed ads + new ones, but never marks
ads that have disappeared inactive — that's what this does, on its own cadence).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import duckdb

from danang_realestate.scrapers.nhatot import NhaTotScraper
from danang_realestate.utils.timeutil import utcnow

logger = logging.getLogger(__name__)


@dataclass
class RescrapeResult:
    checked: int = 0
    price_updated: int = 0
    deactivated: int = 0


def rescrape_active_listings(
    conn: duckdb.DuckDBPyConnection,
    scraper: NhaTotScraper,
    scraped_at: Optional[datetime] = None,
) -> RescrapeResult:
    """Re-fetch each active nhatot listing; record price changes, deactivate vanished ones.

    - Offline (no detail returned) → `is_active = FALSE`.
    - Price changed → append a `listing_price_history` row and update the current price.
    - Otherwise → bump `scraped_at` so the listing reads as freshly seen.
    """
    scraped_at = scraped_at or utcnow()
    active = conn.execute(
        "SELECT listing_id, price FROM raw_listings WHERE is_active = TRUE AND source = 'nhatot'"
    ).fetchall()

    result = RescrapeResult(checked=len(active))
    if not active:
        logger.info("No active nhatot listings to recheck.")
        return result

    logger.info("Rechecking %d active nhatot listings.", len(active))
    for ad_id, db_price in active:
        detail = scraper.fetch_detail(ad_id)
        if not detail:
            conn.execute(
                "UPDATE raw_listings SET is_active = FALSE, scraped_at = ? "
                "WHERE listing_id = ? AND source = 'nhatot'",
                [scraped_at, ad_id],
            )
            result.deactivated += 1
            continue

        price = detail.get("price")
        if price is not None:
            price = int(price)
        size = detail.get("size")
        pps = float(price) / float(size) if price and size else None

        if price != db_price:
            price_change = price - db_price if (price is not None and db_price is not None) else None
            price_change_pct = (
                float(price_change) / float(db_price)
                if (price_change is not None and db_price and db_price > 0)
                else None
            )
            conn.execute(
                """
                INSERT INTO listing_price_history (
                    listing_id, source, price, price_per_sqm, observed_at,
                    previous_price, price_change, price_change_pct
                ) VALUES (?, 'nhatot', ?, ?, ?, ?, ?, ?)
                """,
                [ad_id, price, pps, scraped_at, db_price, price_change, price_change_pct],
            )
            conn.execute(
                "UPDATE raw_listings SET price = ?, price_per_sqm = ?, scraped_at = ? "
                "WHERE listing_id = ? AND source = 'nhatot'",
                [price, pps, scraped_at, ad_id],
            )
            result.price_updated += 1
        else:
            conn.execute(
                "UPDATE raw_listings SET scraped_at = ? WHERE listing_id = ? AND source = 'nhatot'",
                [scraped_at, ad_id],
            )

    logger.info(
        "Rescrape complete: checked=%d price_updated=%d deactivated=%d",
        result.checked, result.price_updated, result.deactivated,
    )
    return result
