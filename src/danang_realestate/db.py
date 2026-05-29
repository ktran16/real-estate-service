from pathlib import Path

import duckdb

from danang_realestate.config import settings


def get_connection() -> duckdb.DuckDBPyConnection:
    """Get a connection to the DuckDB database, ensuring directory exists."""
    db_path = Path(settings.duckdb_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(db_path))

def init_db():
    """Initialize the DuckDB tables if they don't exist."""
    conn = get_connection()
    try:
        # Create raw_listings table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS raw_listings (
                listing_id      BIGINT NOT NULL,
                source          VARCHAR NOT NULL,
                url             VARCHAR,
                title           VARCHAR,
                description     VARCHAR,
                category        INTEGER,
                property_type   VARCHAR,
                transaction_type VARCHAR NOT NULL,
                price           BIGINT,
                price_per_sqm   DOUBLE,
                area_sqm        DOUBLE,
                bedrooms        INTEGER,
                bathrooms       INTEGER,
                num_floors      INTEGER,
                direction       VARCHAR,
                direction_code  INTEGER,
                legal_status    VARCHAR,
                legal_status_code INTEGER,
                furniture       VARCHAR,
                address_raw     VARCHAR,
                ward_code       INTEGER,
                district        VARCHAR,
                ward            VARCHAR,
                lat             DOUBLE,
                lng             DOUBLE,
                account_id      BIGINT,
                account_name    VARCHAR,
                phone           VARCHAR,
                is_broker       BOOLEAN,
                images          VARCHAR[],
                posted_at       TIMESTAMP,
                scraped_at      TIMESTAMP NOT NULL,
                is_active       BOOLEAN DEFAULT TRUE,
                raw_json        JSON,
                PRIMARY KEY (listing_id, source)
            );
        """)

        # Create listing_price_history table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS listing_price_history (
                listing_id      BIGINT NOT NULL,
                source          VARCHAR NOT NULL,
                price           BIGINT,
                price_per_sqm   DOUBLE,
                observed_at     TIMESTAMP NOT NULL,
                previous_price  BIGINT,
                price_change    BIGINT,
                price_change_pct DOUBLE,
                PRIMARY KEY (listing_id, source, observed_at)
            );
        """)

        # Create geocode_cache table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS geocode_cache (
                address_raw     VARCHAR PRIMARY KEY,
                lat             DOUBLE,
                lng             DOUBLE,
                geocoder_source VARCHAR,
                geocoded_at     TIMESTAMP,
                confidence      DOUBLE
            );
        """)

        # Create district_centroids table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS district_centroids (
                district        VARCHAR PRIMARY KEY,
                lat             DOUBLE NOT NULL,
                lng             DOUBLE NOT NULL
            );
        """)

        # Create ward_mapping table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ward_mapping (
                ward_code       INTEGER NOT NULL,
                ward_name       VARCHAR NOT NULL,
                district_name   VARCHAR NOT NULL,
                region_code     INTEGER NOT NULL,
                fetched_at      TIMESTAMP NOT NULL,
                PRIMARY KEY (ward_code, region_code)
            );
        """)

        # Seed static centroids if empty
        res = conn.execute("SELECT COUNT(*) FROM district_centroids").fetchone()
        if res and res[0] == 0:
            centroids = [
                ("Hải Châu", 16.0472, 108.2208),
                ("Thanh Khê", 16.0639, 108.1917),
                ("Sơn Trà", 16.1050, 108.2470),
                ("Ngũ Hành Sơn", 16.0194, 108.2536),
                ("Liên Chiểu", 16.0736, 108.1500),
                ("Cẩm Lệ", 16.0133, 108.2000),
                ("Hòa Vang", 15.9833, 108.0667),
            ]
            conn.executemany(
                "INSERT INTO district_centroids (district, lat, lng) VALUES (?, ?, ?)",
                centroids
            )
            
    finally:
        conn.close()
