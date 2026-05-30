"""Tests for the batdongsan parsing half (no browser involved).

These validate the Vietnamese price/area parsing and card extraction + normalization
LOGIC against a fixture whose markup mirrors batdongsan's documented cards. They do NOT
prove the live selectors are current — that requires capturing real (Cloudflare-cleared)
HTML; see scrapers/batdongsan.py and PROPOSALS.md.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from danang_realestate.models import NormalizedListing
from danang_realestate.scrapers.batdongsan import (
    parse_area_sqm,
    parse_listing_cards,
    parse_price_vnd,
)

FIXTURE_HTML = """
<html><body>
  <div class="re__card-full" data-product-id="12345678">
    <a href="/ban-nha-rieng-da-nang-pr12345678" class="js__card-title">Nhà 3 tầng Hải Châu</a>
    <span class="re__card-config-price">3,5 tỷ</span>
    <span class="re__card-config-area">80 m²</span>
    <span class="re__card-location">Hải Châu, Đà Nẵng</span>
  </div>
  <div class="re__card-full">
    <a href="https://batdongsan.com.vn/cho-thue-can-ho-da-nang-pr87654321" class="js__card-title">Căn hộ Sơn Trà</a>
    <span class="re__card-config-price">12 triệu/tháng</span>
    <span class="re__card-config-area">55,5 m²</span>
    <span class="re__card-location">Sơn Trà, Đà Nẵng</span>
  </div>
  <div class="re__card-full" data-product-id="999">
    <a href="/nha-pr999" class="js__card-title">Đất nền</a>
    <span class="re__card-config-price">Thỏa thuận</span>
    <span class="re__card-config-area">100 m²</span>
    <span class="re__card-location">Ngũ Hành Sơn, Đà Nẵng</span>
  </div>
</body></html>
"""


@pytest.mark.parametrize(
    "text,expected",
    [
        ("3,5 tỷ", 3_500_000_000),
        ("1 tỷ", 1_000_000_000),
        ("850 triệu", 850_000_000),
        ("12 triệu/tháng", 12_000_000),
        ("2 tỷ 500 triệu", 2_500_000_000),
        ("Thỏa thuận", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_price_vnd(text, expected):
    assert parse_price_vnd(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [("80 m²", 80.0), ("55,5 m²", 55.5), ("100m2", 100.0), ("", None), (None, None)],
)
def test_parse_area_sqm(text, expected):
    assert parse_area_sqm(text) == expected


def test_parse_listing_cards_extracts_fields():
    cards = parse_listing_cards(FIXTURE_HTML, transaction_type="sale")
    assert len(cards) == 3

    first = cards[0]
    assert first["listing_id"] == 12345678
    assert first["url"] == "https://batdongsan.com.vn/ban-nha-rieng-da-nang-pr12345678"
    assert first["title"] == "Nhà 3 tầng Hải Châu"
    assert first["price"] == 3_500_000_000
    assert first["area_sqm"] == 80.0
    assert first["address_raw"] == "Hải Châu, Đà Nẵng"

    # id parsed from the URL (-pr<digits>) when no data attribute is present.
    assert cards[1]["listing_id"] == 87654321
    assert cards[1]["price"] == 12_000_000

    # negotiable price -> None, but the card is still emitted.
    assert cards[2]["price"] is None


def test_from_batdongsan_normalizes():
    cards = parse_listing_cards(FIXTURE_HTML, transaction_type="sale")
    scraped_at = datetime(2026, 5, 30, 12, 0, 0)
    listing = NormalizedListing.from_batdongsan(cards[0], scraped_at)

    assert listing.source == "batdongsan"
    assert listing.listing_id == 12345678
    assert listing.price == 3_500_000_000
    assert listing.area_sqm == 80.0
    # price_per_sqm computed = 3.5e9 / 80
    assert listing.price_per_sqm == pytest.approx(43_750_000.0)
    # district normalized from the address.
    assert listing.district == "Hải Châu"
    assert listing.transaction_type == "sale"
    # raw_json round-trips.
    assert "12345678" in listing.raw_json
