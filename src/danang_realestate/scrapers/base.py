from abc import ABC, abstractmethod
from typing import List, Optional

from danang_realestate.models import NormalizedListing


class BaseScraper(ABC):
    @abstractmethod
    def scrape(self, transaction_type: str, limit: Optional[int] = None) -> List[NormalizedListing]:
        """
        Scrape listings from the source.
        transaction_type: 'sale', 'rent', or 'all'
        """
        pass
