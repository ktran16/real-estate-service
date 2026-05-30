"""batdongsan.com.vn scraper (Playwright + HTML parsing).

batdongsan.com.vn sits behind Cloudflare: plain httpx requests return HTTP 403 with a JS
challenge (verified), so — unlike nhatot's JSON gateway — this source needs a real browser.
The design splits cleanly into two halves:

  * `_fetch_rendered()` — the *browser* half. Uses Playwright (the `playwright` extra) to
    load each listing page, clear Cloudflare, and return the rendered HTML. This half CANNOT
    be exercised in CI/sandboxes (no browser, Cloudflare needs a real fingerprint + likely
    residential proxies for sustained runs); see PROPOSALS.md for the hardening plan.

  * `parse_listing_cards()` / `parse_price_vnd()` / `parse_area_sqm()` — the *parsing* half.
    Pure functions over an HTML string, fully unit-tested (tests/test_batdongsan.py) against
    a fixture. Vietnamese price/area parsing here is real and source-agnostic.

IMPORTANT: the CSS selectors below match batdongsan's documented card markup, but the site
changes its DOM periodically. They MUST be re-validated against freshly captured HTML before
a production run — capture a page once Cloudflare is cleared and diff against the fixture.
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional

from bs4 import BeautifulSoup

from danang_realestate.models import NormalizedListing
from danang_realestate.scrapers.base import BaseScraper
from danang_realestate.utils.http import SafeHTTPClient
from danang_realestate.utils.timeutil import utcnow
from danang_realestate.utils.vietnamese import remove_diacritics

logger = logging.getLogger(__name__)

BASE_URL = "https://batdongsan.com.vn"
# Da Nang listing index paths (page N appended as `/p{N}`).
LISTING_PATHS = {
    "sale": "/nha-dat-ban-da-nang",
    "rent": "/nha-dat-cho-thue-da-nang",
}

_NUM = r"(\d+(?:[.,]\d+)?)"

# Substrings that mark a Cloudflare interstitial / challenge page (i.e. the fetch was NOT
# cleared). Used to distinguish "blocked by Cloudflare" from "genuinely no listings".
_CHALLENGE_MARKERS = (
    "just a moment",
    "challenge-platform",
    "cf-chl",
    "cf-browser-verification",
    "/cdn-cgi/challenge",
    "attention required",
    "enable javascript and cookies to continue",
)


class CloudflareChallenge(RuntimeError):
    """Raised when batdongsan returns an uncleared Cloudflare challenge instead of listings."""


def looks_like_challenge(html: Optional[str]) -> bool:
    """True if `html` is a Cloudflare challenge/interstitial rather than a real page.

    A real listing index is tens of KB with card markup; a challenge page is small and carries
    one of the `_CHALLENGE_MARKERS`. We only flag when a marker is present AND no listing cards
    are — so a (future) cleared page that happens to mention Cloudflare isn't misread.
    """
    if not html:
        return True
    lowered = html.lower()
    if not any(marker in lowered for marker in _CHALLENGE_MARKERS):
        return False
    return 'class="re__card' not in lowered and "js__card" not in lowered


def _to_float(num: str) -> float:
    """Parse a Vietnamese-formatted number: '.' = thousands, ',' = decimal. '3,5' -> 3.5."""
    return float(num.replace(".", "").replace(",", "."))


def parse_price_vnd(text: Optional[str]) -> Optional[int]:
    """Parse a batdongsan price string into integer VND.

    Handles 'tỷ' (1e9) and 'triệu' (1e6), the combined '2 tỷ 500 triệu' form, decimal
    commas ('3,5 tỷ'), and rent suffixes ('12 triệu/tháng'). Returns None for negotiable
    prices ('Thỏa thuận') or anything without a recognizable magnitude.
    """
    if not text:
        return None
    t = text.lower().strip()
    if "thoa thuan" in remove_diacritics(t):  # "Thỏa thuận" = negotiable
        return None

    total = 0.0
    found = False
    m = re.search(_NUM + r"\s*tỷ", t)
    if m:
        total += _to_float(m.group(1)) * 1_000_000_000
        found = True
    m = re.search(_NUM + r"\s*triệu", t)
    if m:
        total += _to_float(m.group(1)) * 1_000_000
        found = True
    if not found:
        return None
    return int(round(total))


def parse_area_sqm(text: Optional[str]) -> Optional[float]:
    """Parse an area string like '80 m²' or '80,5 m²' into a float (square metres)."""
    if not text:
        return None
    m = re.search(_NUM + r"\s*m", text.lower())
    if not m:
        return None
    return _to_float(m.group(1))


def _attr_str(value) -> str:
    """Coerce a BeautifulSoup attribute (str | list | None) to a single string."""
    if isinstance(value, list):
        return value[0] if value else ""
    return value or ""


def _first_text(card, selectors: List[str]) -> Optional[str]:
    """Return the stripped text of the first selector that matches in `card`."""
    for sel in selectors:
        el = card.select_one(sel)
        if el and el.get_text(strip=True):
            return el.get_text(strip=True)
    return None


def parse_listing_cards(html: str, transaction_type: str = "sale") -> List[dict]:
    """Extract listing cards from a rendered batdongsan index page into plain dicts.

    Selector strategy is tolerant (several fallbacks per field) because batdongsan rotates
    class names. A card is only emitted if it has both an id and a URL. The returned dicts
    are consumed by `NormalizedListing.from_batdongsan`.
    """
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select(".js__card, .re__card-full, .product-item")
    results: List[dict] = []
    for card in cards:
        link = card.select_one("a[href]")
        if not link:
            continue
        href = _attr_str(link.get("href"))
        url = href if href.startswith("http") else f"{BASE_URL}{href}"

        # Listing id: prefer an explicit data attribute, else trailing digits in the URL
        # (batdongsan URLs end in `-pr<digits>`).
        listing_id = _attr_str(card.get("data-product-id") or link.get("data-product-id"))
        if not listing_id:
            m = re.search(r"-pr(\d+)", href) or re.search(r"(\d{5,})", href)
            listing_id = m.group(1) if m else ""
        if not listing_id:
            continue

        title = _first_text(card, [".js__card-title", ".re__card-title", "h3", "h2"])
        price_text = _first_text(
            card, [".re__card-config-price", ".js__card-config-price", ".price"]
        )
        area_text = _first_text(
            card, [".re__card-config-area", ".js__card-config-area", ".area"]
        )
        address_raw = _first_text(
            card, [".re__card-location", ".js__card-location", ".location"]
        )

        results.append(
            {
                "listing_id": int(listing_id),
                "url": url,
                "title": title,
                "transaction_type": transaction_type,
                "price": parse_price_vnd(price_text),
                "area_sqm": parse_area_sqm(area_text),
                "address_raw": address_raw,
                "posted_at": None,  # not reliably present on index cards
                "_raw": {"price_text": price_text, "area_text": area_text},
            }
        )
    return results


class BatDongSanScraper(BaseScraper):
    SOURCE = "batdongsan"
    # batdongsan paginates ~20-30 cards/page; cap pages so a bad selector can't loop forever.
    MAX_PAGES = 40

    def __init__(self, client: Optional[SafeHTTPClient] = None):
        # `client` is accepted for registry symmetry but unused: fetching goes through
        # Playwright, not the httpx-based SafeHTTPClient (which Cloudflare 403s).
        self.client = client

    def scrape(self, transaction_type: str, limit: Optional[int] = None) -> List[NormalizedListing]:
        tx_types = ["sale", "rent"] if transaction_type == "all" else [transaction_type]
        scraped_at = utcnow()
        listings: List[NormalizedListing] = []
        seen: set[int] = set()

        for tx in tx_types:
            path = LISTING_PATHS.get(tx)
            if not path:
                logger.warning("Unknown transaction_type '%s' for batdongsan; skipping.", tx)
                continue
            for page in range(1, self.MAX_PAGES + 1):
                url = f"{BASE_URL}{path}" + (f"/p{page}" if page > 1 else "")
                html = self._fetch_rendered(url)
                # A challenge page parses to 0 cards too — fail loudly so a blocked run is never
                # mistaken for "no listings" (which would silently wipe the source's data).
                if looks_like_challenge(html):
                    raise CloudflareChallenge(
                        f"batdongsan returned an uncleared Cloudflare challenge at {url}. "
                        "A real browser fingerprint + (likely) a residential proxy are needed; "
                        "see PROPOSALS.md P0 #1."
                    )
                cards = parse_listing_cards(html, transaction_type=tx)
                if not cards:
                    if page == 1:
                        # Not a challenge, yet the first page yielded nothing → the selectors
                        # are almost certainly stale, NOT a genuinely empty result set.
                        logger.warning(
                            "batdongsan %s page 1 parsed 0 cards but is not a Cloudflare "
                            "challenge — selectors likely stale; re-validate against fresh HTML.",
                            tx,
                        )
                    break  # past the last page (or selectors need re-validation)
                for card in cards:
                    if card["listing_id"] in seen:
                        continue
                    seen.add(card["listing_id"])
                    listings.append(NormalizedListing.from_batdongsan(card, scraped_at))
                    if limit and len(listings) >= limit:
                        return listings
        return listings

    def _fetch_rendered(self, url: str) -> str:
        """Load `url` in a real browser and return rendered HTML (clears Cloudflare).

        Imported lazily so the module (and its tested parsers) load without Playwright.
        Raises a clear error if the `playwright` extra / browser binaries are missing.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - exercised only with the extra
            raise RuntimeError(
                "batdongsan scraping needs Playwright. Install it with "
                "`uv sync --extra playwright && uv run playwright install chromium`."
            ) from exc

        with sync_playwright() as p:  # pragma: no cover - needs a real browser + network
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    locale="vi-VN",
                    viewport={"width": 1366, "height": 900},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                # Give Cloudflare's JS challenge time to resolve, then the cards to render.
                page.wait_for_timeout(5_000)
                try:
                    page.wait_for_selector(".js__card, .re__card-full", timeout=15_000)
                except Exception:
                    logger.warning("No listing cards rendered at %s (challenge/selectors?).", url)
                return page.content()
            finally:
                browser.close()
