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

    def test_batdongsan_scrape_wiring(self):
        # Patch the browser half (_fetch_rendered) so scrape() is exercised end-to-end
        # without Playwright/Cloudflare: page 1 returns two cards, page 2 is empty (stop).
        from unittest.mock import patch

        page1 = """
        <div class="re__card-full" data-product-id="111">
          <a href="/x-pr111" class="js__card-title">A</a>
          <span class="re__card-config-price">2 tỷ</span>
          <span class="re__card-config-area">50 m²</span>
          <span class="re__card-location">Hải Châu, Đà Nẵng</span>
        </div>
        <div class="re__card-full" data-product-id="222">
          <a href="/y-pr222" class="js__card-title">B</a>
          <span class="re__card-config-price">3 tỷ</span>
          <span class="re__card-config-area">60 m²</span>
          <span class="re__card-location">Sơn Trà, Đà Nẵng</span>
        </div>
        """
        scraper = BatDongSanScraper()
        # page 2 is a valid empty page (an empty string now reads as a blocked/challenge fetch).
        empty = "<html><body>no more results</body></html>"
        with patch.object(scraper, "_fetch_rendered", side_effect=[page1, empty]):
            listings = scraper.scrape(transaction_type="sale", limit=10)

        self.assertEqual([listing.listing_id for listing in listings], [111, 222])
        self.assertEqual(listings[0].source, "batdongsan")
        self.assertEqual(listings[0].price, 2_000_000_000)

    def test_batdongsan_requires_playwright(self):
        # With no browser available, the fetch half fails with a clear, actionable error.
        import importlib.util

        if importlib.util.find_spec("playwright") is not None:
            self.skipTest("playwright extra installed; the missing-extra path can't be exercised")
        with self.assertRaises(RuntimeError) as ctx:
            BatDongSanScraper()._fetch_rendered("https://batdongsan.com.vn/nha-dat-ban-da-nang")
        self.assertIn("Playwright", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
