import logging
import random
import time
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from danang_realestate.config import settings

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 13; SM-S901B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-A536B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 13; Xiaomi 12T) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile Safari/537.36",
]

has_curl_cffi = False
try:
    from curl_cffi import requests as curl_requests
    has_curl_cffi = True
except ImportError:
    pass

class SafeHTTPClient:
    def __init__(
        self,
        delay_min: Optional[int] = None,
        delay_max: Optional[int] = None,
        use_curl_cffi: bool = False,
    ):
        # Fall back to configured values (.env / Settings) when not explicitly overridden.
        self.delay_min = delay_min if delay_min is not None else settings.scrape_delay_min
        self.delay_max = delay_max if delay_max is not None else settings.scrape_delay_max
        self.use_curl_cffi = use_curl_cffi and has_curl_cffi
        # Include the configured user agent in the rotation pool if one is set.
        self.user_agents = list(USER_AGENTS)
        if settings.scrape_user_agent and settings.scrape_user_agent not in self.user_agents:
            self.user_agents.append(settings.scrape_user_agent)
        self._httpx_client = None

    def get_client(self):
        if not self._httpx_client:
            self._httpx_client = httpx.Client(http2=True, follow_redirects=True, timeout=15.0)
        return self._httpx_client

    def _sleep_delay(self):
        delay = random.uniform(self.delay_min, self.delay_max)
        logger.info(f"Delaying request for {delay:.2f}s...")
        time.sleep(delay)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=15),
        reraise=True
    )
    def get(self, url: str, params: Optional[dict] = None, extra_headers: Optional[dict] = None) -> dict:
        """Perform a GET request with retry, backoff, and user-agent rotation."""
        self._sleep_delay()

        headers = {
            "User-Agent": random.choice(self.user_agents),
            "Accept": "application/json",
            "Referer": "https://www.chotot.com/",
        }
        if extra_headers:
            headers.update(extra_headers)

        if self.use_curl_cffi:
            logger.info(f"GET (curl_cffi) -> {url} with params {params}")
            resp = curl_requests.get(url, params=params, headers=headers, impersonate="chrome110", timeout=15)
            resp.raise_for_status()
            return resp.json()
        else:
            client = self.get_client()
            logger.info(f"GET (httpx) -> {url} with params {params}")
            resp = client.get(url, params=params, headers=headers)
            if resp.status_code == 429:
                logger.warning("Received HTTP 429 Too Many Requests")
                raise httpx.HTTPStatusError("429 Too Many Requests", request=resp.request, response=resp)
            resp.raise_for_status()
            return resp.json()

    def close(self):
        if self._httpx_client:
            self._httpx_client.close()
            self._httpx_client = None
