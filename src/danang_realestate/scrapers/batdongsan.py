"""batdongsan.com.vn scraper (placeholder).

batdongsan.com.vn sits behind Cloudflare with aggressive anti-bot protection: plain
HTTP requests return HTTP 403 with a Cloudflare challenge page (verified), so the
nhatot-style httpx/JSON approach does not work here. A working implementation needs a
real browser via Playwright (the `playwright` extra) to clear the JS challenge, and
likely residential proxies for sustained scraping.

This class implements the `BaseScraper` interface so it can be registered and selected
like any other source; `scrape()` raises `NotImplementedError` until the Playwright-based
implementation lands. See ORCHESTRATION.md / the improvement plan for the approach:

    1. Launch Playwright (chromium, stealth UA + viewport) to load the Da Nang listing
       pages (e.g. https://batdongsan.com.vn/nha-dat-ban-da-nang) and clear Cloudflare.
    2. Parse the rendered listing cards (title, price, area, address, posted date, url).
    3. Map each to `NormalizedListing` with source="batdongsan" (add a `from_batdongsan`
       classmethod mirroring `from_nhatot`).
    4. Reuse the existing loader / geocoder / dbt path unchanged.
"""
from typing import List, Optional

from danang_realestate.models import NormalizedListing
from danang_realestate.scrapers.base import BaseScraper
from danang_realestate.utils.http import SafeHTTPClient


class BatDongSanScraper(BaseScraper):
    SOURCE = "batdongsan"

    def __init__(self, client: Optional[SafeHTTPClient] = None):
        self.client = client

    def scrape(self, transaction_type: str, limit: Optional[int] = None) -> List[NormalizedListing]:
        raise NotImplementedError(
            "batdongsan.com.vn is Cloudflare-protected and requires a Playwright-based "
            "scraper (install the 'playwright' extra). Not yet implemented — see "
            "scrapers/batdongsan.py and the improvement plan."
        )
