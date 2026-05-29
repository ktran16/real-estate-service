import logging
from typing import Set

from danang_realestate.models import NhaTotAd
from danang_realestate.utils.http import SafeHTTPClient

logger = logging.getLogger(__name__)

class SchemaDriftReport:
    def __init__(self, model_fields: Set[str], api_fields: Set[str]):
        self.model_fields = model_fields
        self.api_fields = api_fields
        
        # Fields in Pydantic model but not in API response
        # (Exclude fields that are expected to be optional/often missing, but list them)
        self.missing_in_api = model_fields - api_fields
        
        # Fields in API response but not in Pydantic model
        self.extra_in_api = api_fields - model_fields

    def has_drift(self) -> bool:
        # We consider it drift if there are extra fields, or if critical fields are missing
        critical_fields = {"ad_id", "list_id", "account_id", "price", "size", "type"}
        critical_missing = self.missing_in_api.intersection(critical_fields)
        return len(self.extra_in_api) > 0 or len(critical_missing) > 0

    def print_summary(self):
        print("--- Schema drift report ---")
        print(f"Model fields defined: {len(self.model_fields)}")
        print(f"API fields observed: {len(self.api_fields)}")
        
        if self.missing_in_api:
            print(f"⚠️ Model fields missing in API response ({len(self.missing_in_api)}):")
            for f in sorted(self.missing_in_api):
                print(f"  - {f}")
        else:
            print("✅ All model fields present in API.")
            
        if self.extra_in_api:
            print(f"⚠️ New/Extra fields in API response not in model ({len(self.extra_in_api)}):")
            for f in sorted(self.extra_in_api):
                print(f"  - {f}")
        else:
            print("✅ No extra/untracked fields in API.")

def validate_schema(client: SafeHTTPClient) -> SchemaDriftReport:
    """Fetch a sample ad and check for schema drift against NhaTotAd model."""
    url = "https://gateway.chotot.com/v1/public/ad-listing"
    params = {
        "cg": 1020,  # House category (always active)
        "region_v2": 3017,
        "limit": 1,
        "st": "s"
    }
    
    logger.info("Fetching sample listing from nhatot.com API for schema validation...")
    data = client.get(url, params=params)
    
    if not data or not isinstance(data, dict):
        raise ValueError("Invalid response format received from API")
        
    ads = data.get("ads", [])
    if not ads:
        raise ValueError("No ads found in the validation search response.")
        
    sample_ad = ads[0]
    
    # Get model fields
    model_fields = set(NhaTotAd.model_fields.keys())
    
    # Get api keys
    api_fields = set(sample_ad.keys())
    
    report = SchemaDriftReport(model_fields, api_fields)
    
    if report.has_drift():
        logger.warning(
            f"Schema drift detected! Extra fields in API: {report.extra_in_api}. "
            f"Missing critical fields: {report.missing_in_api.intersection({'ad_id', 'list_id', 'account_id', 'price', 'size', 'type'})}"
        )
    else:
        logger.info("Schema validation successful. No critical drift detected.")
        
    return report
