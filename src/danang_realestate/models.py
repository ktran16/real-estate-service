import json
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from danang_realestate.utils.vietnamese import clean_district, normalize_text


class NhaTotParam(BaseModel):
    id: str
    label: str
    value: str

class NhaTotSellerInfo(BaseModel):
    avatar: Optional[str] = None
    full_name: Optional[str] = None
    live_ads: Optional[int] = None
    sold_ads: Optional[int] = None

class NhaTotAd(BaseModel):
    ad_id: int
    list_id: int
    account_id: int
    account_name: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    category: Optional[int] = None
    category_name: Optional[str] = None
    price: Optional[float] = None
    price_string: Optional[str] = None
    size: Optional[float] = None
    rooms: Optional[int] = None
    toilets: Optional[int] = None
    floors: Optional[int] = None
    direction: Optional[int] = None
    property_legal_document: Optional[int] = None
    type: Optional[str] = None  # "s", "u", etc.
    images: Optional[List[str]] = Field(default_factory=list)
    list_time: Optional[int] = None  # milliseconds since epoch
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    area_name: Optional[str] = None  # District name (e.g. Quận Ngũ Hành Sơn)
    ward_name: Optional[str] = None  # Ward name (e.g. Phường Hoà Hải)
    ward: Optional[int] = None  # Numeric ward code
    region_v2: Optional[int] = None
    phone: Optional[str] = None
    params: Optional[List[NhaTotParam]] = Field(default_factory=list)
    seller_info: Optional[NhaTotSellerInfo] = None

class NormalizedListing(BaseModel):
    listing_id: int
    source: str
    url: str
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[int] = None
    property_type: Optional[str] = None
    transaction_type: str
    price: Optional[int] = None
    price_per_sqm: Optional[float] = None
    area_sqm: Optional[float] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    num_floors: Optional[int] = None
    direction: Optional[str] = None
    direction_code: Optional[int] = None
    legal_status: Optional[str] = None
    legal_status_code: Optional[int] = None
    furniture: Optional[str] = None
    address_raw: Optional[str] = None
    ward_code: Optional[int] = None
    district: Optional[str] = None
    ward: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    account_id: Optional[int] = None
    account_name: Optional[str] = None
    phone: Optional[str] = None
    is_broker: Optional[bool] = False
    images: List[str] = Field(default_factory=list)
    posted_at: Optional[datetime] = None
    scraped_at: datetime
    is_active: bool = True
    raw_json: str  # JSON-serialized string for DuckDB JSON type

    @classmethod
    def from_nhatot(cls, ad: NhaTotAd, scraped_at: datetime) -> "NormalizedListing":
        # Property type mapping
        # 1010 -> apartment, 1020 -> house, 1030 -> office/commercial, 1040 -> land, 1050 -> room
        property_type_map = {
            1010: "apartment",
            1020: "house",
            1030: "office",
            1040: "land",
            1050: "room"
        }
        prop_type = property_type_map.get(ad.category, "other") if ad.category is not None else "other"

        # Transaction type mapping (s = sale, k = sale, u = rent, h = rent)
        tx_type = "rent" if ad.type in ["u", "h"] else "sale"

        # Construct URL
        # example: https://www.nhatot.com/mua-ban-bat-dong-san-quan-ngu-hanh-son-da-nang/132592579.htm
        url = f"https://www.nhatot.com/vi/mua-ban-nha-dat/{ad.list_id}.htm"

        # Extracted fields from params list
        direction_name = None
        furniture_val = None
        
        # In nhatot, direction code mapping
        direction_map = {
            1: "Đông",
            2: "Tây",
            3: "Nam",
            4: "Bắc",
            5: "Đông Bắc",
            6: "Tây Bắc",
            7: "Đông Nam",
            8: "Tây Nam"
        }
        
        legal_map = {
            1: "Sổ hồng/ Sổ đỏ",
            2: "Hợp đồng mua bán",
            3: "Đang chờ sổ",
            4: "Giấy tờ khác"
        }

        # Look in params for detailed strings if available
        for p in (ad.params or []):
            if p.id == "direction":
                direction_name = p.value
            elif p.id == "furniture":
                furniture_val = p.value

        if not direction_name and ad.direction in direction_map:
            direction_name = f"Hướng {direction_map[ad.direction]}"

        legal_status_name = (
            legal_map.get(ad.property_legal_document)
            if ad.property_legal_document is not None
            else None
        )

        # Build raw address: ward + district + Da Nang
        addr_parts = []
        if ad.ward_name:
            addr_parts.append(ad.ward_name)
        if ad.area_name:
            addr_parts.append(ad.area_name)
        addr_parts.append("Đà Nẵng")
        address_raw = ", ".join(addr_parts)

        # District normalization
        normalized_district = clean_district(ad.area_name)

        # Price per sqm
        price_per_sqm = None
        if ad.price and ad.size and ad.size > 0:
            price_per_sqm = float(ad.price) / float(ad.size)

        # Broker detection flag
        is_broker = False
        if ad.seller_info and ad.seller_info.live_ads and ad.seller_info.live_ads >= 5:
            is_broker = True

        posted_at = None
        if ad.list_time:
            posted_at = datetime.fromtimestamp(ad.list_time / 1000.0)

        return cls(
            listing_id=ad.ad_id,
            source="nhatot",
            url=url,
            title=normalize_text(ad.subject),
            description=ad.body,
            category=ad.category,
            property_type=prop_type,
            transaction_type=tx_type,
            price=int(ad.price) if ad.price is not None else None,
            price_per_sqm=price_per_sqm,
            area_sqm=ad.size,
            bedrooms=ad.rooms,
            bathrooms=ad.toilets,
            num_floors=ad.floors,
            direction=direction_name,
            direction_code=ad.direction,
            legal_status=legal_status_name,
            legal_status_code=ad.property_legal_document,
            furniture=furniture_val,
            address_raw=address_raw,
            ward_code=ad.ward,
            district=normalized_district,
            ward=ad.ward_name,
            lat=ad.latitude,
            lng=ad.longitude,
            account_id=ad.account_id,
            account_name=ad.account_name,
            phone=ad.phone,
            is_broker=is_broker,
            images=ad.images or [],
            posted_at=posted_at,
            scraped_at=scraped_at,
            is_active=True,
            raw_json=ad.model_dump_json()
        )

    @classmethod
    def from_batdongsan(cls, card: dict, scraped_at: datetime) -> "NormalizedListing":
        """Build a NormalizedListing from a parsed batdongsan.com.vn listing card.

        `card` is the dict produced by `scrapers.batdongsan.parse_listing_cards`:
        listing_id, url, title, transaction_type, price (int VND or None), area_sqm,
        address_raw, posted_at (datetime or None). batdongsan exposes far fewer
        structured fields than nhatot's JSON API (no rooms/legal/coords), so many
        columns stay None and are filled later (e.g. district by geocoding).
        """
        price = card.get("price")
        area = card.get("area_sqm")
        price_per_sqm = None
        if price and area and area > 0:
            price_per_sqm = float(price) / float(area)

        address_raw = card.get("address_raw")
        district = clean_district(address_raw) if address_raw else None

        return cls(
            listing_id=int(card["listing_id"]),
            source="batdongsan",
            url=card["url"],
            title=normalize_text(card.get("title")) or None,
            transaction_type=card.get("transaction_type", "sale"),
            price=int(price) if price is not None else None,
            price_per_sqm=price_per_sqm,
            area_sqm=area,
            address_raw=address_raw,
            district=district,
            posted_at=card.get("posted_at"),
            scraped_at=scraped_at,
            is_active=True,
            raw_json=json.dumps(card, default=str, ensure_ascii=False),
        )
