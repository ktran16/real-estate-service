from typing import Optional

from danang_realestate.scrapers.base import BaseScraper
from danang_realestate.scrapers.batdongsan import BatDongSanScraper
from danang_realestate.scrapers.nhatot import NhaTotScraper
from danang_realestate.utils.http import SafeHTTPClient

# Registry of available sources, keyed by the `--source` CLI value / SCRAPE_SOURCE env.
SCRAPERS = {
    "nhatot": NhaTotScraper,
    "batdongsan": BatDongSanScraper,
}


def get_scraper(source: str, client: Optional[SafeHTTPClient] = None) -> BaseScraper:
    """Return a scraper instance for `source`. Raises ValueError for unknown sources."""
    try:
        scraper_cls = SCRAPERS[source]
    except KeyError:
        raise ValueError(
            f"Unknown scraper source '{source}'. Available: {', '.join(sorted(SCRAPERS))}"
        )
    return scraper_cls(client)


__all__ = ["BaseScraper", "NhaTotScraper", "BatDongSanScraper", "SCRAPERS", "get_scraper"]
