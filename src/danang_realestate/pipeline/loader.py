import logging
from typing import List

import duckdb

from danang_realestate.models import NormalizedListing
from danang_realestate.pipeline.price_tracker import detect_price_changes

logger = logging.getLogger(__name__)

def load_listings(conn: duckdb.DuckDBPyConnection, listings: List[NormalizedListing]):
    """Upsert listings into raw_listings and write price changes/observations to history."""
    if not listings:
        logger.info("No listings to load.")
        return

    # The same ad can surface under multiple nhatot categories within a single scrape, so the
    # batch may contain duplicate (listing_id, source) rows. raw_listings tolerates that
    # (INSERT OR REPLACE), but listing_price_history does not: two rows for a brand-new listing
    # share the PK (listing_id, source, observed_at) and the plain INSERT then violates the
    # constraint, rolling back the whole load. Dedupe up front, keeping the last occurrence.
    seen = set()
    deduped: List[NormalizedListing] = []
    for listing in reversed(listings):
        key = (listing.listing_id, listing.source)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(listing)
    deduped.reverse()
    if len(deduped) != len(listings):
        logger.info(
            "Deduplicated batch: %d listings -> %d unique (listing_id, source).",
            len(listings), len(deduped),
        )
    listings = deduped

    # First, detect price changes before we modify raw_listings
    new_obs, price_updates = detect_price_changes(conn, listings)
    history_to_insert = new_obs + price_updates

    # Start database transaction
    conn.execute("BEGIN TRANSACTION")
    try:
        # 1. Upsert listings into raw_listings
        # We use INSERT OR REPLACE because primary key is (listing_id, source)
        upsert_query = """
            INSERT OR REPLACE INTO raw_listings (
                listing_id, source, url, title, description, category, property_type,
                transaction_type, price, price_per_sqm, area_sqm, bedrooms, bathrooms,
                num_floors, direction, direction_code, legal_status, legal_status_code,
                furniture, address_raw, ward_code, district, ward, lat, lng,
                account_id, account_name, phone, is_broker, images, posted_at,
                scraped_at, is_active, raw_json
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?
            )
        """
        
        listings_data = []
        for listing in listings:
            listings_data.append((
                listing.listing_id, listing.source, listing.url, listing.title, listing.description, listing.category, listing.property_type,
                listing.transaction_type, listing.price, listing.price_per_sqm, listing.area_sqm, listing.bedrooms, listing.bathrooms,
                listing.num_floors, listing.direction, listing.direction_code, listing.legal_status, listing.legal_status_code,
                listing.furniture, listing.address_raw, listing.ward_code, listing.district, listing.ward, listing.lat, listing.lng,
                listing.account_id, listing.account_name, listing.phone, listing.is_broker, listing.images, listing.posted_at,
                listing.scraped_at, listing.is_active, listing.raw_json
            ))
            
        conn.executemany(upsert_query, listings_data)
        logger.info(f"Upserted {len(listings)} listings into raw_listings.")

        # 2. Insert price history records
        if history_to_insert:
            history_query = """
                INSERT INTO listing_price_history (
                    listing_id, source, price, price_per_sqm, observed_at,
                    previous_price, price_change, price_change_pct
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """
            history_data = []
            for h in history_to_insert:
                history_data.append((
                    h.listing_id, h.source, h.price, h.price_per_sqm, h.observed_at,
                    h.previous_price, h.price_change, h.price_change_pct
                ))
            conn.executemany(history_query, history_data)
            logger.info(
                f"Recorded {len(history_to_insert)} price history entries "
                f"({len(new_obs)} new listings, {len(price_updates)} price changes)."
            )

        conn.execute("COMMIT")
    except Exception as e:
        conn.execute("ROLLBACK")
        logger.error(f"Transaction failed, rolled back: {e}")
        raise e
