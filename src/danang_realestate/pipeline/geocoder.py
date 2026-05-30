import logging
import time
from typing import Optional, Tuple

import duckdb
from geopy.exc import GeocoderServiceError, GeocoderTimedOut
from geopy.geocoders import Nominatim

from danang_realestate.config import settings
from danang_realestate.utils.http import SafeHTTPClient
from danang_realestate.utils.timeutil import utcnow

logger = logging.getLogger(__name__)

GOONG_GEOCODE_URL = "https://rsapi.goong.io/Geocode"

# Confidence per geocoding tier (persisted to geocode_cache.confidence). Address-level
# geocoders are trusted more than the district-centroid fallback, which is only a rough
# placeholder. `regeocode_low_confidence` uses this to find rows worth re-attempting.
CONFIDENCE = {
    "nominatim": 0.9,
    "goong": 0.8,
    "district_centroid": 0.3,
    "none": 0.0,
}

class Geocoder:
    def __init__(
        self,
        conn: duckdb.DuckDBPyConnection,
        http_client: Optional[SafeHTTPClient] = None,
    ):
        self.conn = conn
        self.geolocator = Nominatim(user_agent=settings.nominatim_user_agent, timeout=5.0)
        self.goong_api_key = settings.goong_api_key
        # Lazily created (only when a Goong key is configured); injectable for tests.
        self._http = http_client
        self.last_query_time = 0.0

    def _get_http(self) -> SafeHTTPClient:
        if self._http is None:
            self._http = SafeHTTPClient()
        return self._http

    def _geocode_goong(self, query_address: str) -> Tuple[Optional[float], Optional[float]]:
        """Tier 3: Goong Maps (Vietnamese geocoder). Only used when a key is configured."""
        if not self.goong_api_key:
            return None, None
        try:
            data = self._get_http().get(
                GOONG_GEOCODE_URL,
                params={"address": query_address, "api_key": self.goong_api_key},
            )
            results = (data or {}).get("results") or []
            if results:
                loc = results[0].get("geometry", {}).get("location", {})
                lat, lng = loc.get("lat"), loc.get("lng")
                if lat is not None and lng is not None:
                    logger.info(f"Goong resolved: {query_address} -> ({lat}, {lng})")
                    return lat, lng
            logger.warning(f"Goong could not resolve: {query_address}")
        except Exception as e:
            logger.warning(f"Goong lookup failed: {e}")
        return None, None

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

        # 3. Tier 3: Goong Maps (only if configured and Nominatim failed)
        if (lat is None or lng is None) and self.goong_api_key:
            g_lat, g_lng = self._geocode_goong(query_address)
            if g_lat is not None and g_lng is not None:
                lat, lng = g_lat, g_lng
                source = "goong"

        # 4. Fallback to District Centroid if address geocoders fail
        if lat is None or lng is None:
            lat, lng = self.get_district_centroid(district)
            source = "district_centroid"
            if lat is not None:
                logger.info(f"Centroid Fallback used for district '{district}': ({lat}, {lng})")
            else:
                logger.warning(f"No fallback found for district '{district}'")

        # No tier resolved coordinates at all.
        if lat is None or lng is None:
            source = "none"

        # 5. Save to Cache with a tier-appropriate confidence (see CONFIDENCE).
        confidence = CONFIDENCE.get(source, 0.0)
        try:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO geocode_cache (
                    address_raw, lat, lng, geocoder_source, geocoded_at, confidence
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [address_raw, lat, lng, source, utcnow(), confidence]
            )
        except Exception as e:
            logger.error(f"Error writing to geocode cache: {e}")

        return lat, lng, source

    def regeocode_low_confidence(
        self, confidence_below: float = 0.5, limit: Optional[int] = None
    ) -> int:
        """Re-attempt addresses that only got a low-confidence result (e.g. district centroid).

        A better tier (Nominatim/Goong) may now resolve an address that previously fell back to
        a centroid. For each low-confidence cache entry we drop the cached row (so `geocode`
        re-resolves instead of returning the stale low-confidence hit), re-geocode, and if it
        upgrades to an address-level tier, update the cache (done by `geocode`) and any
        `raw_listings` rows using that address. Returns the count upgraded.
        """
        query = "SELECT address_raw FROM geocode_cache WHERE confidence < ?"
        params: list = [confidence_below]
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        rows = self.conn.execute(query, params).fetchall()
        if not rows:
            logger.info("No low-confidence geocode entries to re-attempt.")
            return 0

        upgraded = 0
        for (address_raw,) in rows:
            drow = self.conn.execute(
                "SELECT district FROM raw_listings WHERE address_raw = ? LIMIT 1",
                [address_raw],
            ).fetchone()
            district = drow[0] if drow else None
            # Drop the cached low-confidence row so geocode() actually re-resolves.
            self.conn.execute(
                "DELETE FROM geocode_cache WHERE address_raw = ?", [address_raw]
            )
            lat, lng, source = self.geocode(address_raw, district)
            if source in ("nominatim", "goong") and lat is not None and lng is not None:
                self.conn.execute(
                    "UPDATE raw_listings SET lat = ?, lng = ? WHERE address_raw = ?",
                    [lat, lng, address_raw],
                )
                upgraded += 1
        logger.info(
            "Re-geocoded %d low-confidence addresses; %d upgraded to address-level.",
            len(rows), upgraded,
        )
        return upgraded

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

    def close(self):
        """Close the lazily-created HTTP client (used for Goong), if any."""
        if self._http is not None:
            self._http.close()
            self._http = None
