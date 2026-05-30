import logging
from typing import List, Optional

from danang_realestate.models import NhaTotAd, NormalizedListing
from danang_realestate.scrapers.base import BaseScraper
from danang_realestate.utils.http import SafeHTTPClient
from danang_realestate.utils.timeutil import utcnow

logger = logging.getLogger(__name__)

class NhaTotScraper(BaseScraper):
    def __init__(self, client: Optional[SafeHTTPClient] = None):
        self.client = client or SafeHTTPClient()
        self.base_url = "https://gateway.chotot.com/v1/public/ad-listing"
        self.detail_url = "https://gateway.chotot.com/v1/public/ad-detail"

    def scrape(self, transaction_type: str, limit: Optional[int] = None) -> List[NormalizedListing]:
        """
        Scrape nhatot.com for listings in Da Nang.
        
        transaction_type: 'sale', 'rent', or 'all'
        limit: Maximum total listings to scrape across all pages and categories.
        """
        region_code = 3017  # Da Nang region code (region=3 + region_v2=17 -> 3017)
        
        # Determine the transaction types (st) to query
        if transaction_type == "sale":
            st_options = ["s"]  # Cần bán
        elif transaction_type == "rent":
            st_options = ["u"]  # Cho thuê
        else:
            st_options = ["s", "u"]

        # Categories mapping to real estate
        # 1010: Căn hộ/Chung cư, 1020: Nhà ở, 1030: Văn phòng/Mặt bằng, 1040: Đất, 1050: Phòng trọ
        categories = [1010, 1020, 1030, 1040, 1050]
        
        all_listings: List[NormalizedListing] = []
        scraped_at = utcnow()

        for cg in categories:
            for st in st_options:
                if limit and len(all_listings) >= limit:
                    break

                logger.info(f"Starting scrape for category cg={cg}, type st={st} in Da Nang...")
                
                offset = 0
                page_limit = 20  # nhatot API default limit
                
                while True:
                    if limit and len(all_listings) >= limit:
                        break

                    params = {
                        "cg": cg,
                        "region_v2": region_code,
                        "limit": page_limit,
                        "o": offset,
                        "st": st
                    }
                    
                    try:
                        data = self.client.get(self.base_url, params=params)
                    except Exception as e:
                        logger.error(f"HTTP request failed for cg={cg}, st={st}, offset={offset}: {e}")
                        break
                    
                    if not data or not isinstance(data, dict):
                        logger.warning("Empty or malformed JSON response received.")
                        break
                        
                    ads_raw = data.get("ads", [])
                    if not ads_raw:
                        logger.info(f"No ads returned for cg={cg}, st={st} at offset {offset}. Done with this segment.")
                        break
                        
                    logger.info(f"Successfully retrieved {len(ads_raw)} ads (cg={cg}, st={st}, offset={offset})")
                    
                    for ad_dict in ads_raw:
                        try:
                            # 1. Parse and validate using raw ad Pydantic model
                            raw_ad = NhaTotAd(**ad_dict)
                            # 2. Transform to database-normalized format
                            normalized = NormalizedListing.from_nhatot(raw_ad, scraped_at)
                            all_listings.append(normalized)
                        except Exception as e:
                            logger.error(f"Error parsing ad ID {ad_dict.get('ad_id', 'unknown')}: {e}")
                            continue

                    if len(ads_raw) < page_limit:
                        # Reached last page
                        break
                        
                    offset += page_limit

        logger.info(f"Finished scraping. Total listings collected: {len(all_listings)}")
        return all_listings[:limit] if limit else all_listings

    def fetch_detail(self, ad_id: int) -> Optional[dict]:
        """Fetch the full, unmasked details of a single listing (optional helper)."""
        url = f"{self.detail_url}/{ad_id}"
        try:
            data = self.client.get(url)
            if data and isinstance(data, dict):
                return data.get("ad", data)
        except Exception as e:
            logger.error(f"Failed to fetch detail for ad {ad_id}: {e}")
        return None
