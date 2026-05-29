import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import duckdb

from danang_realestate.models import NormalizedListing

logger = logging.getLogger(__name__)

class PriceHistoryRecord:
    def __init__(
        self,
        listing_id: int,
        source: str,
        price: Optional[int],
        price_per_sqm: Optional[float],
        observed_at: datetime,
        previous_price: Optional[int] = None,
        price_change: Optional[int] = None,
        price_change_pct: Optional[float] = None
    ):
        self.listing_id = listing_id
        self.source = source
        self.price = price
        self.price_per_sqm = price_per_sqm
        self.observed_at = observed_at
        self.previous_price = previous_price
        self.price_change = price_change
        self.price_change_pct = price_change_pct

def get_existing_prices(conn: duckdb.DuckDBPyConnection, listing_ids: List[int], source: str) -> Dict[int, Optional[int]]:
    """Query current prices of existing listings in bulk."""
    if not listing_ids:
        return {}
        
    # Chunk list if too large to avoid expression limits
    results = {}
    chunk_size = 500
    for i in range(0, len(listing_ids), chunk_size):
        chunk = listing_ids[i:i+chunk_size]
        placeholders = ", ".join("?" for _ in chunk)
        query = f"SELECT listing_id, price FROM raw_listings WHERE source = ? AND listing_id IN ({placeholders})"
        rows = conn.execute(query, [source] + chunk).fetchall()
        for lid, price in rows:
            results[lid] = price
    return results

def detect_price_changes(
    conn: duckdb.DuckDBPyConnection, 
    listings: List[NormalizedListing]
) -> Tuple[List[PriceHistoryRecord], List[PriceHistoryRecord]]:
    """
    Compare listings against existing DB state.
    Returns:
        - new_observations: List of PriceHistoryRecord for newly created listings.
        - price_updates: List of PriceHistoryRecord for listings with price changes.
    """
    if not listings:
        return [], []
        
    source = listings[0].source
    listing_ids = [listing.listing_id for listing in listings]
    existing_prices = get_existing_prices(conn, listing_ids, source)

    new_observations = []
    price_updates = []

    for listing in listings:
        observed_at = listing.scraped_at

        if listing.listing_id not in existing_prices:
            # First observation of this listing
            rec = PriceHistoryRecord(
                listing_id=listing.listing_id,
                source=listing.source,
                price=listing.price,
                price_per_sqm=listing.price_per_sqm,
                observed_at=observed_at,
                previous_price=None,
                price_change=None,
                price_change_pct=None
            )
            new_observations.append(rec)
        else:
            prev_price = existing_prices[listing.listing_id]
            if listing.price != prev_price:
                # Price has changed!
                price_change = None
                price_change_pct = None

                if listing.price is not None and prev_price is not None:
                    price_change = listing.price - prev_price
                    if prev_price > 0:
                        price_change_pct = float(price_change) / float(prev_price)

                rec = PriceHistoryRecord(
                    listing_id=listing.listing_id,
                    source=listing.source,
                    price=listing.price,
                    price_per_sqm=listing.price_per_sqm,
                    observed_at=observed_at,
                    previous_price=prev_price,
                    price_change=price_change,
                    price_change_pct=price_change_pct
                )
                price_updates.append(rec)
                pct_str = f"{price_change_pct:+.1%}" if price_change_pct is not None else "N/A"
                logger.info(
                    f"Price change detected for listing {listing.listing_id} ({listing.source}): "
                    f"{prev_price} -> {listing.price} ({pct_str})"
                )
                
    return new_observations, price_updates
