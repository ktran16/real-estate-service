import unittest

from danang_realestate.models import NhaTotAd, NhaTotParam, NhaTotSellerInfo, NormalizedListing
from danang_realestate.utils.timeutil import utcnow
from danang_realestate.utils.vietnamese import clean_district, normalize_text, remove_diacritics


class TestNormalizer(unittest.TestCase):
    def test_remove_diacritics(self):
        self.assertEqual(remove_diacritics("Đà Nẵng"), "Da Nang")
        self.assertEqual(remove_diacritics("Hải Châu"), "Hai Chau")
        self.assertEqual(remove_diacritics("Ngũ Hành Sơn"), "Ngu Hanh Son")

    def test_normalize_text(self):
        self.assertEqual(normalize_text("  Quận  Hải   Châu  "), "Quận Hải Châu")
        self.assertEqual(normalize_text(""), "")

    def test_clean_district(self):
        self.assertEqual(clean_district("Quận Hải Châu"), "Hải Châu")
        self.assertEqual(clean_district("Q. Sơn Trà"), "Sơn Trà")
        self.assertEqual(clean_district("Hai Chau"), "Hải Châu")
        self.assertEqual(clean_district("Ngũ Hành Sơn"), "Ngũ Hành Sơn")
        self.assertEqual(clean_district("Random District"), None)

    def test_from_nhatot_mapping(self):
        # Create a mock raw ad
        ad = NhaTotAd(
            ad_id=12345,
            list_id=67890,
            account_id=999,
            account_name="Nguyen Van A",
            subject="Bán nhà mặt tiền Lê Duẩn",
            body="Mô tả nhà bán...",
            category=1020,  # House
            category_name="Nhà ở",
            price=2500000000.0,
            price_string="2.5 tỷ",
            size=100.0,
            rooms=3,
            toilets=2,
            floors=2,
            direction=8,  # Southwest
            property_legal_document=1,  # Red/Pink book
            type="s",  # Sale
            images=["https://image1.jpg", "https://image2.jpg"],
            list_time=1700000000000,
            latitude=16.0472,
            longitude=108.2208,
            area_name="Quận Hải Châu",
            ward_name="Phường Hải Châu I",
            ward=123,
            region_v2=3017,
            phone="0905123456",
            params=[
                NhaTotParam(id="direction", label="Hướng", value="Hướng Tây Nam"),
                NhaTotParam(id="furniture", label="Nội thất", value="Đầy đủ")
            ],
            seller_info=NhaTotSellerInfo(live_ads=10, sold_ads=2)
        )
        
        scraped_at = utcnow()
        normalized = NormalizedListing.from_nhatot(ad, scraped_at)
        
        self.assertEqual(normalized.listing_id, 12345)
        self.assertEqual(normalized.source, "nhatot")
        self.assertEqual(normalized.property_type, "house")
        self.assertEqual(normalized.transaction_type, "sale")
        self.assertEqual(normalized.price, 2500000000)
        self.assertEqual(normalized.price_per_sqm, 25000000.0)
        self.assertEqual(normalized.bedrooms, 3)
        self.assertEqual(normalized.bathrooms, 2)
        self.assertEqual(normalized.num_floors, 2)
        self.assertEqual(normalized.direction, "Hướng Tây Nam")
        self.assertEqual(normalized.legal_status, "Sổ hồng/ Sổ đỏ")
        self.assertEqual(normalized.furniture, "Đầy đủ")
        self.assertEqual(normalized.district, "Hải Châu")
        self.assertEqual(normalized.ward, "Phường Hải Châu I")
        self.assertEqual(normalized.is_broker, True)  # live_ads >= 5

if __name__ == "__main__":
    unittest.main()
