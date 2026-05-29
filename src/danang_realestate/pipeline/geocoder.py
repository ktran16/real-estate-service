import logging
import time
from typing import Optional, Tuple

import duckdb
from geopy.exc import GeocoderServiceError, GeocoderTimedOut
from geopy.geocoders import Nominatim

from danang_realestate.config import settings
from danang_realestate.utils.timeutil import utcnow

logger = logging.getLogger(__name__)

class Geocoder:
    def __init__(self, conn: duckdb.DuckDBPyConnection):
        self.conn = conn
        self.geolocator = Nominatim(user_agent=settings.nominatim_user_agent, timeout=5.0)
        self.last_query_time = 0.0

    def _sleep_rate_limit(self):
        """Ensure at least 1.0 second between Nominatim requests."""
        now = time.time()
        elapsed = now - self.last_query_time
        if elapsed < 1.0:
            sleep_time = 1.0 - elapsed
            time.sleep(sleep_time)
        self.last_query_time = time.time()

    def get_district_centroid(self, district: Optional[str]) -> Tuple[Optional[float], Optional[float]]:
        """Fetch static centroid fallback coordinates for a district."""
        if not district:
            return None, None
        try:
            row = self.conn.execute(
                "SELECT lat, lng FROM district_centroids WHERE district = ?",
                [district]
            ).fetchone()
            if row:
                return row[0], row[1]
        except Exception as e:
            logger.error(f"Error fetching district centroid: {e}")
        return None, None

    def geocode(self, address_raw: Optional[str], district: Optional[str]) -> Tuple[Optional[float], Optional[float], str]:
        """
        Geocode a raw address. Check Cache -> Nominatim -> District Fallback.
        Returns: (latitude, longitude, source)
        """
        if not address_raw:
            return None, None, "none"

        # 1. Lookup in Cache
        try:
            row = self.conn.execute(
                "SELECT lat, lng, geocoder_source FROM geocode_cache WHERE address_raw = ?",
                [address_raw]
            ).fetchone()
            if row:
                return row[0], row[1], row[2]
        except Exception as e:
            logger.error(f"Error checking cache: {e}")

        # 2. Try Nominatim (OpenStreetMap)
        lat, lng = None, None
        source = "nominatim"
        
        # Include Đà Nẵng, Vietnam to scope search properly
        query_address = f"{address_raw}, Đà Nẵng, Vietnam"
        logger.info(f"Geocoding with Nominatim: {query_address}")
        
        try:
            self._sleep_rate_limit()
            location = self.geolocator.geocode(query_address)
            if location:
                lat, lng = location.latitude, location.longitude
                logger.info(f"Nominatim resolved: {address_raw} -> ({lat}, {lng})")
            else:
                logger.warning(f"Nominatim could not resolve: {address_raw}")
        except (GeocoderTimedOut, GeocoderServiceError) as e:
            logger.warning(f"Nominatim lookup timed out or failed: {e}")

        # 3. Fallback to District Centroid if Nominatim fails
        if lat is None or lng is None:
            lat, lng = self.get_district_centroid(district)
            source = "district_centroid"
            if lat is not None:
                logger.info(f"Centroid Fallback used for district '{district}': ({lat}, {lng})")
            else:
                logger.warning(f"No fallback found for district '{district}'")

        # 4. Save to Cache
        try:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO geocode_cache (
                    address_raw, lat, lng, geocoder_source, geocoded_at, confidence
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [address_raw, lat, lng, source, utcnow(), 1.0 if source == "nominatim" else 0.0]
            )
        except Exception as e:
            logger.error(f"Error writing to geocode cache: {e}")
            
        return lat, lng, source

    def geocode_pending_listings(self):
        """Geocode all listings in raw_listings table that lack coordinates."""
        try:
            rows = self.conn.execute(
                """
                SELECT listing_id, source, address_raw, district
                FROM raw_listings
                WHERE lat IS NULL OR lng IS NULL
                """
            ).fetchall()
        except Exception as e:
            logger.error(f"Error querying pending geocodes: {e}")
            return
        
        if not rows:
            logger.info("No listings found with missing coordinates.")
            return

        logger.info(f"Found {len(rows)} listings to geocode.")
        
        updated_count = 0
        for lid, src, addr, dist in rows:
            lat, lng, geocode_src = self.geocode(addr, dist)
            if lat is not None and lng is not None:
                try:
                    self.conn.execute(
                        """
                        UPDATE raw_listings
                        SET lat = ?, lng = ?
                        WHERE listing_id = ? AND source = ?
                        """,
                        [lat, lng, lid, src]
                    )
                    updated_count += 1
                except Exception as e:
                    logger.error(f"Failed to update listing {lid} ({src}) coordinates: {e}")
                    
        logger.info(f"Successfully geocoded and updated {updated_count} listings.")
