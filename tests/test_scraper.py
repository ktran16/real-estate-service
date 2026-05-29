import unittest
from unittest.mock import MagicMock

from danang_realestate.scrapers import get_scraper
from danang_realestate.scrapers.batdongsan import BatDongSanScraper
from danang_realestate.scrapers.nhatot import NhaTotScraper


def _sample_ad(ad_id: int, price: float = 2_000_000_000.0):
    return {
        "ad_id": ad_id,
        "list_id": ad_id + 1,
        "account_id": 999,
        "account_name": "Test Seller",
        "subject": "Bán nhà",
        "category": 1020,
        "price": price,
        "size": 100.0,
        "type": "s",
        "area_name": "Quận Hải Châu",
        "ward_name": "Phường Hải Châu I",
        "region_v2": 3017,
    }


class TestNhaTotScraper(unittest.TestCase):
    def test_scrape_parses_mocked_response(self):
        # Hermetic: inject a mock HTTP client so no network call / sleep happens.
        client = MagicMock()
        # First call returns one ad (< page_limit -> segment ends); subsequent
        # category/type combinations return no ads.
        client.get.side_effect = [{"ads": [_sample_ad(1)]}] + [{"ads": []}] * 20

        scraper = NhaTotScraper(client=client)
        listings = scraper.scrape(transaction_type="sale", limit=10)

        self.assertEqual(len(listings), 1)
        listing = listings[0]
        self.assertEqual(listing.listing_id, 1)
        self.assertEqual(listing.source, "nhatot")
        self.assertEqual(listing.property_type, "house")
        self.assertEqual(listing.transaction_type, "sale")
        self.assertEqual(listing.district, "Hải Châu")
        self.assertTrue(client.get.called)

    def test_scrape_respects_limit(self):
        client = MagicMock()
        # Always return a full page so pagination would continue unbounded
        # without the limit guard.
        full_page = {"ads": [_sample_ad(i) for i in range(20)]}
        client.get.return_value = full_page

        scraper = NhaTotScraper(client=client)
        listings = scraper.scrape(transaction_type="sale", limit=5)

        self.assertEqual(len(listings), 5)

    def test_scrape_skips_unparseable_ads(self):
        client = MagicMock()
        # One valid ad and one malformed ad (missing required fields).
        client.get.side_effect = [
            {"ads": [_sample_ad(1), {"subject": "broken, no ids"}]}
        ] + [{"ads": []}] * 20

        scraper = NhaTotScraper(client=client)
        listings = scraper.scrape(transaction_type="sale", limit=10)

        # Malformed ad is skipped, valid one kept.
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].listing_id, 1)


class TestScraperRegistry(unittest.TestCase):
    def test_get_scraper_nhatot(self):
        self.assertIsInstance(get_scraper("nhatot"), NhaTotScraper)

    def test_get_scraper_batdongsan(self):
        self.assertIsInstance(get_scraper("batdongsan"), BatDongSanScraper)

    def test_get_scraper_unknown_raises(self):
        with self.assertRaises(ValueError):
            get_scraper("does-not-exist")

    def test_batdongsan_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            BatDongSanScraper().scrape(transaction_type="sale", limit=1)


if __name__ == "__main__":
    unittest.main()
