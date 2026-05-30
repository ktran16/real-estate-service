import unittest
from unittest.mock import MagicMock

import duckdb

from danang_realestate.pipeline.geocoder import Geocoder
from danang_realestate.utils.timeutil import utcnow


class TestGeocoder(unittest.TestCase):
    def setUp(self):
        # Create an in-memory DuckDB connection for testing
        self.conn = duckdb.connect(":memory:")

        # Create tables
        self.conn.execute("""
            CREATE TABLE geocode_cache (
                address_raw     VARCHAR PRIMARY KEY,
                lat             DOUBLE,
                lng             DOUBLE,
                geocoder_source VARCHAR,
                geocoded_at     TIMESTAMP,
                confidence      DOUBLE
            );
        """)
        self.conn.execute("""
            CREATE TABLE district_centroids (
                district        VARCHAR PRIMARY KEY,
                lat             DOUBLE NOT NULL,
                lng             DOUBLE NOT NULL
            );
        """)

        # Seed test centroids
        self.conn.execute(
            "INSERT INTO district_centroids (district, lat, lng) VALUES ('Hải Châu', 16.0472, 108.2208)"
        )

        self.geocoder = Geocoder(self.conn)
        # Hermetic: never hit the real Nominatim service in tests. Individual
        # tests configure the return value as needed.
        self.geocoder.geolocator.geocode = MagicMock(return_value=None)

    def tearDown(self):
        self.conn.close()

    def test_get_district_centroid(self):
        lat, lng = self.geocoder.get_district_centroid("Hải Châu")
        self.assertEqual(lat, 16.0472)
        self.assertEqual(lng, 108.2208)

        lat, lng = self.geocoder.get_district_centroid("NonExistent")
        self.assertIsNone(lat)
        self.assertIsNone(lng)

    def test_geocode_cache_hit(self):
        # Insert a record into cache
        self.conn.execute(
            """
            INSERT INTO geocode_cache (address_raw, lat, lng, geocoder_source, geocoded_at, confidence)
            VALUES ('123 Test St', 12.3456, 78.9012, 'test_source', ?, 1.0)
            """,
            [utcnow()]
        )

        lat, lng, source = self.geocoder.geocode("123 Test St", "Hải Châu")
        self.assertEqual(lat, 12.3456)
        self.assertEqual(lng, 78.9012)
        self.assertEqual(source, "test_source")
        # Cache hit must not call the geocoding service at all.
        self.geocoder.geolocator.geocode.assert_not_called()

    def test_geocode_resolved_by_nominatim(self):
        # Nominatim returns a location -> coords come from the service.
        self.geocoder.geolocator.geocode.return_value = MagicMock(latitude=16.07, longitude=108.22)

        lat, lng, source = self.geocoder.geocode("Some real street", "Hải Châu")
        self.assertEqual(lat, 16.07)
        self.assertEqual(lng, 108.22)
        self.assertEqual(source, "nominatim")

    def test_geocode_goong_tier(self):
        # Nominatim fails; a Goong key + mocked Goong client resolves it.
        self.geocoder.goong_api_key = "test-key"
        mock_http = MagicMock()
        mock_http.get.return_value = {
            "results": [{"geometry": {"location": {"lat": 16.05, "lng": 108.21}}}]
        }
        self.geocoder._http = mock_http

        lat, lng, source = self.geocoder.geocode("Goong-resolvable address", "Hải Châu")
        self.assertEqual(lat, 16.05)
        self.assertEqual(lng, 108.21)
        self.assertEqual(source, "goong")
        mock_http.get.assert_called_once()
        # Cached with full confidence (address-level geocode, not centroid).
        row = self.conn.execute(
            "SELECT geocoder_source, confidence FROM geocode_cache WHERE address_raw = 'Goong-resolvable address'"
        ).fetchone()
        self.assertEqual(row[0], "goong")
        self.assertEqual(row[1], 0.8)  # goong tier confidence

    def test_geocode_no_goong_without_key(self):
        # Without a key, Goong is skipped entirely and we fall back to centroid.
        self.geocoder.goong_api_key = ""  # hermetic: ignore any ambient .env key
        mock_http = MagicMock()
        self.geocoder._http = mock_http  # should never be used

        lat, lng, source = self.geocoder.geocode("Some address", "Hải Châu")
        self.assertEqual(source, "district_centroid")
        mock_http.get.assert_not_called()

    def test_geocode_fallback_to_centroid(self):
        # Nominatim resolves nothing (mocked to None) -> district centroid fallback.
        lat, lng, source = self.geocoder.geocode("Unresolvable address", "Hải Châu")

        self.assertEqual(lat, 16.0472)
        self.assertEqual(lng, 108.2208)
        self.assertEqual(source, "district_centroid")

        # Verify it was added to the cache
        cache_row = self.conn.execute(
            "SELECT lat, lng, geocoder_source, confidence FROM geocode_cache WHERE address_raw = 'Unresolvable address'"
        ).fetchone()
        self.assertIsNotNone(cache_row)
        self.assertEqual(cache_row[0], 16.0472)
        self.assertEqual(cache_row[2], "district_centroid")
        self.assertEqual(cache_row[3], 0.3)  # centroid tier confidence

    def test_regeocode_low_confidence_upgrades(self):
        # A raw_listings table with one listing that previously fell back to the centroid.
        self.conn.execute(
            """
            CREATE TABLE raw_listings (
                listing_id BIGINT, source VARCHAR, address_raw VARCHAR,
                district VARCHAR, lat DOUBLE, lng DOUBLE
            )
            """
        )
        self.conn.execute(
            "INSERT INTO raw_listings VALUES (1, 'nhatot', '15 Real St', 'Hải Châu', 16.0472, 108.2208)"
        )
        # Low-confidence centroid cache entry for that address.
        self.conn.execute(
            """
            INSERT INTO geocode_cache (address_raw, lat, lng, geocoder_source, geocoded_at, confidence)
            VALUES ('15 Real St', 16.0472, 108.2208, 'district_centroid', ?, 0.3)
            """,
            [utcnow()],
        )
        # This time Nominatim resolves a precise location.
        self.geocoder.geolocator.geocode.return_value = MagicMock(latitude=16.06, longitude=108.21)

        upgraded = self.geocoder.regeocode_low_confidence(confidence_below=0.5)
        self.assertEqual(upgraded, 1)

        # raw_listings coords upgraded to the precise location.
        row = self.conn.execute(
            "SELECT lat, lng FROM raw_listings WHERE listing_id = 1"
        ).fetchone()
        self.assertEqual(row, (16.06, 108.21))
        # cache entry now address-level (higher confidence).
        crow = self.conn.execute(
            "SELECT geocoder_source, confidence FROM geocode_cache WHERE address_raw = '15 Real St'"
        ).fetchone()
        self.assertEqual(crow[0], "nominatim")
        self.assertEqual(crow[1], 0.9)

    def test_regeocode_low_confidence_no_rows(self):
        # Nothing below the threshold -> no work, returns 0.
        self.assertEqual(self.geocoder.regeocode_low_confidence(confidence_below=0.5), 0)


if __name__ == "__main__":
    unittest.main()
