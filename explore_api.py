#!/usr/bin/env python3
"""
Phase 0: nhatot.com (Chợ Tốt) API Exploration Script

Probes the public gateway API to discover and verify:
  1. Region codes (find Da Nang's region_v2 value)
  2. Category codes (sale vs rent, sub-categories)
  3. Ward codes for Da Nang
  4. Search endpoint behavior (pagination, params, response shape)
  5. Detail endpoint response schema
  6. Rate limit behavior

Usage:
    pip install httpx[http2] rich
    python explore_api.py

Outputs:
    - Structured report to stdout
    - Raw JSON responses saved to data/api_exploration/
"""

import json
import time
from datetime import datetime
from pathlib import Path

import httpx

# ── Config ───────────────────────────────────────────────────────────────────

API_BASE = "https://gateway.chotot.com"
V1_PUBLIC = f"{API_BASE}/v1/public"
V2_PUBLIC = f"{API_BASE}/v2/public"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    "Referer": "https://www.chotot.com/",
}

OUTPUT_DIR = Path("data/api_exploration")
DELAY_BETWEEN_REQUESTS = 3  # seconds

# ── Helpers ──────────────────────────────────────────────────────────────────

def save_json(data: dict | list, filename: str) -> Path:
    """Save JSON response to the exploration output directory."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def safe_request(client: httpx.Client, url: str, label: str) -> dict | list | None:
    """Make a GET request with error handling and delay."""
    print(f"\n{'─' * 60}")
    print(f"🔍 {label}")
    print(f"   GET {url}")

    try:
        resp = client.get(url, headers=HEADERS, timeout=15.0)
        print(f"   Status: {resp.status_code}")
        print(f"   Content-Type: {resp.headers.get('content-type', 'N/A')}")
        print(f"   Content-Length: {len(resp.content)} bytes")

        if resp.status_code == 429:
            print("   ⚠️  RATE LIMITED (HTTP 429) — stopping to avoid ban")
            retry_after = resp.headers.get("Retry-After", "unknown")
            print(f"   Retry-After: {retry_after}")
            return None

        if resp.status_code >= 400:
            print(f"   ❌ Error response: {resp.text[:500]}")
            return None

        data = resp.json()
        return data

    except httpx.TimeoutException:
        print("   ❌ Request timed out")
        return None
    except json.JSONDecodeError:
        print(f"   ❌ Non-JSON response: {resp.text[:300]}")
        return None
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return None
    finally:
        time.sleep(DELAY_BETWEEN_REQUESTS)


def print_header(title: str):
    print(f"\n{'═' * 60}")
    print(f"  {title}")
    print(f"{'═' * 60}")


def summarize_keys(data: dict, indent: int = 4) -> str:
    """Recursively summarize keys and types of a dict."""
    lines = []
    prefix = " " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}: {{...}} ({len(value)} keys)")
            lines.append(summarize_keys(value, indent + 4))
        elif isinstance(value, list):
            if value:
                sample = value[0]
                type_hint = type(sample).__name__
                lines.append(f"{prefix}{key}: [{type_hint}, ...] ({len(value)} items)")
            else:
                lines.append(f"{prefix}{key}: [] (empty)")
        else:
            type_name = type(value).__name__
            val_preview = str(value)[:80]
            lines.append(f"{prefix}{key}: {type_name} = {val_preview}")
    return "\n".join(lines)


# ── Exploration Steps ────────────────────────────────────────────────────────

def explore_regions(client: httpx.Client) -> int | None:
    """Step 1: Find all region codes and identify Da Nang."""
    print_header("STEP 1: Discover Region Codes")

    danang_region = None

    # Try multiple known endpoint patterns
    endpoints = [
        (f"{V1_PUBLIC}/chapy-pro/regions", "v1/chapy-pro/regions"),
        (f"{V2_PUBLIC}/chapy-pro/regions", "v2/chapy-pro/regions"),
        (f"{V1_PUBLIC}/regions", "v1/regions"),
    ]

    for url, label in endpoints:
        data = safe_request(client, url, f"Regions ({label})")
        if data is not None:
            save_json(data, f"regions_{label.replace('/', '_')}.json")

            # Search for Da Nang in the response
            regions = data if isinstance(data, list) else data.get("regions", data.get("data", []))

            if isinstance(regions, list):
                print(f"\n   📊 Found {len(regions)} regions")
                for r in regions:
                    name = ""
                    code = None

                    if isinstance(r, dict):
                        # Try common key patterns
                        name = r.get("name", r.get("region_name", r.get("label", "")))
                        code = r.get("id", r.get("region_id", r.get("code", r.get("region_v2", None))))
                    elif isinstance(r, str):
                        name = r

                    name_lower = str(name).lower()
                    if any(kw in name_lower for kw in ["đà nẵng", "da nang", "danang"]):
                        danang_region = code
                        print(f"   🎯 FOUND DA NANG: code={code}, name='{name}'")

                # Print first 5 as sample
                print("\n   Sample regions (first 5):")
                for r in regions[:5]:
                    print(f"      {r}")
            elif isinstance(regions, dict):
                print(f"\n   📊 Response is a dict with keys: {list(regions.keys())[:10]}")
                # Check if Da Nang is a key
                for key, val in regions.items():
                    val_str = str(val).lower() if val else ""
                    key_str = str(key).lower()
                    if any(kw in val_str or kw in key_str for kw in ["đà nẵng", "da nang", "danang"]):
                        danang_region = key
                        print(f"   🎯 FOUND DA NANG: key={key}, value={val}")

            break  # Use first successful endpoint

    if danang_region is None:
        print("\n   ⚠️  Could not auto-detect Da Nang region code.")
        print("   Trying known candidates: 48 (VN admin code), 4, 5")

        # Try a search with candidate codes to see which returns Da Nang results
        for candidate in [48, 4, 5]:
            url = f"{V1_PUBLIC}/ad-listing?cg=1000&region_v2={candidate}&limit=1"
            data = safe_request(client, url, f"Test region_v2={candidate}")
            if data and isinstance(data, dict):
                total = data.get("total", 0)
                ads = data.get("ads", [])
                if total > 0 and ads:
                    sample_region = ads[0].get("region_name", ads[0].get("area_name", "unknown"))
                    print(f"   region_v2={candidate}: total={total}, sample region_name='{sample_region}'")
                    if any(kw in str(sample_region).lower() for kw in ["đà nẵng", "da nang"]):
                        danang_region = candidate
                        print(f"   🎯 CONFIRMED: Da Nang region_v2 = {candidate}")
                        break
                else:
                    print(f"   region_v2={candidate}: total={total} (no results)")

    return danang_region


def explore_categories(client: httpx.Client) -> dict:
    """Step 2: Discover category codes for real estate."""
    print_header("STEP 2: Discover Category Codes")

    results = {}

    endpoints = [
        (f"{V1_PUBLIC}/chapy-pro/categories", "v1/chapy-pro/categories"),
        (f"{V2_PUBLIC}/chapy-pro/categories", "v2/chapy-pro/categories"),
    ]

    for url, label in endpoints:
        data = safe_request(client, url, f"Categories ({label})")
        if data is not None:
            save_json(data, f"categories_{label.replace('/', '_')}.json")

            # Look for real estate categories
            categories = data if isinstance(data, list) else data.get("categories", data.get("data", []))

            if isinstance(categories, list):
                print(f"\n   📊 Found {len(categories)} categories")
                for cat in categories:
                    if isinstance(cat, dict):
                        name = cat.get("name", cat.get("label", ""))
                        code = cat.get("id", cat.get("code", cat.get("cg", "")))
                        name_lower = str(name).lower()
                        # Look for real estate related categories
                        if any(kw in name_lower for kw in [
                            "bất động sản", "bat dong san", "nhà đất", "nha dat",
                            "real estate", "property", "1000", "1010", "1020"
                        ]):
                            results[code] = name
                            print(f"   🏠 RE Category: code={code}, name='{name}'")

                        # Also check sub-categories
                        subcats = cat.get("subcategories", cat.get("children", cat.get("sub", [])))
                        if isinstance(subcats, list):
                            for sub in subcats:
                                if isinstance(sub, dict):
                                    sub_name = sub.get("name", sub.get("label", ""))
                                    sub_code = sub.get("id", sub.get("code", sub.get("cg", "")))
                                    if any(kw in str(sub_name).lower() for kw in [
                                        "bất động sản", "nhà", "đất", "căn hộ", "villa",
                                        "mua bán", "cho thuê", "sale", "rent"
                                    ]):
                                        results[sub_code] = sub_name
                                        print(f"   🏠 RE Sub-category: code={sub_code}, name='{sub_name}'")

            break  # Use first successful endpoint

    # Also try brute-force: test known candidate category codes
    print("\n   📊 Testing known category code candidates...")
    candidate_codes = [1000, 1010, 1020, 1030, 1040, 1001, 1002, 1003, 1004]
    for cg in candidate_codes:
        url = f"{V1_PUBLIC}/ad-listing?cg={cg}&limit=1"
        data = safe_request(client, url, f"Test cg={cg}")
        if data and isinstance(data, dict):
            total = data.get("total", 0)
            ads = data.get("ads", [])
            if total > 0:
                # Try to figure out what type of listing this is
                sample_cat = ads[0].get("category", "N/A") if ads else "N/A"
                sample_type = ads[0].get("type", ads[0].get("category_name", "N/A")) if ads else "N/A"
                sample_subject = ads[0].get("subject", "")[:60] if ads else ""
                print(f"   cg={cg}: total={total}, sample_cat={sample_cat}, type={sample_type}")
                print(f"           subject: '{sample_subject}'")
                results[cg] = f"total={total}"
            else:
                print(f"   cg={cg}: total=0 (no results)")
        elif data is None:
            print(f"   cg={cg}: request failed")

    return results


def explore_wards(client: httpx.Client, region_code: int) -> list:
    """Step 3: Get ward codes for Da Nang."""
    print_header(f"STEP 3: Discover Ward Codes (region={region_code})")

    endpoints = [
        (f"{V1_PUBLIC}/chapy-pro/wards?region={region_code}", "v1 wards"),
        (f"{V1_PUBLIC}/chapy-pro/wards?region_v2={region_code}", "v1 wards (region_v2)"),
    ]

    wards = []
    for url, label in endpoints:
        data = safe_request(client, url, f"Wards ({label})")
        if data is not None:
            save_json(data, f"wards_region_{region_code}_{label.replace(' ', '_')}.json")

            ward_list = data if isinstance(data, list) else data.get("wards", data.get("data", []))
            if isinstance(ward_list, list) and ward_list:
                print(f"\n   📊 Found {len(ward_list)} wards")
                for w in ward_list[:10]:
                    print(f"      {w}")
                wards = ward_list
                break

    return wards


def explore_search(client: httpx.Client, region_code: int, cg: int = 1000) -> dict:
    """Step 4: Explore the search endpoint in detail."""
    print_header(f"STEP 4: Search Endpoint Deep Dive (region={region_code}, cg={cg})")

    schema_info = {}

    # 4a: Basic search — small result set
    url = f"{V1_PUBLIC}/ad-listing?cg={cg}&region_v2={region_code}&limit=3"
    data = safe_request(client, url, "Basic search (limit=3)")
    if data and isinstance(data, dict):
        save_json(data, "search_basic.json")

        # Top-level keys
        print(f"\n   📊 Top-level response keys: {list(data.keys())}")
        print(f"   total: {data.get('total', 'N/A')}")

        # Pagination info
        paging = data.get("paging", {})
        if paging:
            print(f"   paging: {paging}")

        # Analyze first ad's schema
        ads = data.get("ads", [])
        if ads:
            first_ad = ads[0]
            print(f"\n   📊 First ad keys ({len(first_ad)} fields):")
            print(summarize_keys(first_ad))

            # Check for params nesting
            params = first_ad.get("params", first_ad.get("parameters", {}))
            if isinstance(params, dict) and params:
                print(f"\n   📊 Nested 'params' object ({len(params)} fields):")
                print(summarize_keys(params))

            schema_info["ad_keys"] = list(first_ad.keys())
            schema_info["params_keys"] = list(params.keys()) if isinstance(params, dict) else []
            schema_info["sample_ad"] = first_ad

    # 4b: Test pagination with offset
    url_offset = f"{V1_PUBLIC}/ad-listing?cg={cg}&region_v2={region_code}&limit=2&o=2"
    data_offset = safe_request(client, url_offset, "Pagination test (o=2, limit=2)")
    if data_offset and isinstance(data_offset, dict):
        save_json(data_offset, "search_offset_test.json")
        ads = data_offset.get("ads", [])
        if ads:
            first_id = ads[0].get("ad_id", ads[0].get("list_id", "N/A"))
            print(f"   First ad_id at offset=2: {first_id}")
            schema_info["offset_pagination"] = True

    # 4c: Test pagination with page param
    url_page = f"{V1_PUBLIC}/ad-listing?cg={cg}&region_v2={region_code}&limit=2&page=1"
    data_page = safe_request(client, url_page, "Pagination test (page=1, limit=2)")
    if data_page and isinstance(data_page, dict):
        save_json(data_page, "search_page_test.json")
        ads = data_page.get("ads", [])
        if ads:
            first_id = ads[0].get("ad_id", ads[0].get("list_id", "N/A"))
            print(f"   First ad_id at page=1: {first_id}")
            schema_info["page_pagination"] = True

    # 4d: Test key_param_included
    url_kpi = f"{V1_PUBLIC}/ad-listing?cg={cg}&region_v2={region_code}&limit=1&key_param_included=true"
    data_kpi = safe_request(client, url_kpi, "Test key_param_included=true")
    if data_kpi and isinstance(data_kpi, dict):
        save_json(data_kpi, "search_key_param_included.json")
        ads = data_kpi.get("ads", [])
        if ads:
            keys_with = set(ads[0].keys())
            keys_without = set(schema_info.get("ad_keys", []))
            extra_keys = keys_with - keys_without
            if extra_keys:
                print(f"   🆕 Extra fields with key_param_included: {extra_keys}")
            else:
                print("   No extra fields detected with key_param_included")

    return schema_info


def explore_detail(client: httpx.Client, ad_id: int) -> dict:
    """Step 5: Explore the detail endpoint for a single listing."""
    print_header(f"STEP 5: Detail Endpoint (ad_id={ad_id})")

    detail_schema = {}

    # Try both known endpoint patterns
    endpoints = [
        (f"{V1_PUBLIC}/ad-detail/{ad_id}", "v1/ad-detail"),
        (f"{V1_PUBLIC}/ad-listing/{ad_id}", "v1/ad-listing/{id}"),
    ]

    for url, label in endpoints:
        data = safe_request(client, url, f"Detail ({label})")
        if data and isinstance(data, dict):
            save_json(data, f"detail_{label.replace('/', '_')}_{ad_id}.json")

            # The actual ad data might be nested
            ad = data.get("ad", data.get("data", data))

            print(f"\n   📊 Detail response keys ({len(ad)} fields):")
            print(summarize_keys(ad))

            params = ad.get("params", ad.get("parameters", {}))
            if isinstance(params, dict) and params:
                print(f"\n   📊 Detail 'params' ({len(params)} fields):")
                print(summarize_keys(params))

            detail_schema["keys"] = list(ad.keys())
            detail_schema["params_keys"] = list(params.keys()) if isinstance(params, dict) else []
            break

    return detail_schema


def explore_rate_limits(client: httpx.Client, region_code: int, cg: int = 1000):
    """Step 6: Gentle rate limit probing (5 rapid requests)."""
    print_header("STEP 6: Rate Limit Probe (5 rapid requests, 1s apart)")

    url = f"{V1_PUBLIC}/ad-listing?cg={cg}&region_v2={region_code}&limit=1"

    print("   ⚠️  Sending 5 requests with 1-second delays...")
    print("   (Will stop immediately if rate limited)")

    for i in range(5):
        start = time.time()
        try:
            resp = client.get(url, headers=HEADERS, timeout=10.0)
            elapsed = time.time() - start
            print(f"   Request {i+1}: status={resp.status_code}, time={elapsed:.2f}s")

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After", "unknown")
                print(f"   🚫 RATE LIMITED after {i+1} requests! Retry-After: {retry_after}")
                break
            elif resp.status_code >= 400:
                print(f"   ❌ Error {resp.status_code} after {i+1} requests")
                break

        except Exception as e:
            print(f"   ❌ Request {i+1} failed: {e}")
            break

        time.sleep(1)  # 1 second between rapid requests

    print("\n   ✅ Completed rate limit probe")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  nhatot.com API Exploration — Phase 0")
    print(f"  Started: {datetime.now().isoformat()}")
    print("=" * 60)

    with httpx.Client(http2=True, follow_redirects=True) as client:

        # Step 1: Find Da Nang region code
        region_code = explore_regions(client)
        if region_code is None:
            print("\n❌ Could not determine Da Nang region code.")
            print("   Try manually with browser DevTools.")
            print("   Using fallback: region_v2=48")
            region_code = 48

        print(f"\n✅ Using region_v2={region_code} for remaining exploration")

        # Step 2: Discover category codes
        categories = explore_categories(client)

        # Step 3: Get ward codes
        wards = explore_wards(client, region_code)

        # Step 4: Explore search endpoint
        # Use the first working category code, default to 1000
        search_cg = 1000
        if categories:
            # Pick one that had results
            for cg, info in categories.items():
                if isinstance(info, str) and "total=" in info:
                    total = int(info.split("total=")[1])
                    if total > 0:
                        search_cg = cg
                        break

        schema = explore_search(client, region_code, search_cg)

        # Step 5: Explore detail endpoint (use first ad_id from search)
        sample_ad = schema.get("sample_ad", {})
        ad_id = sample_ad.get("ad_id", sample_ad.get("list_id"))
        if ad_id:
            detail_schema = explore_detail(client, ad_id)
        else:
            print("\n⚠️  No ad_id found — skipping detail endpoint exploration")
            detail_schema = {}

        # Step 6: Gentle rate limit probe
        explore_rate_limits(client, region_code, search_cg)

    # ── Summary Report ───────────────────────────────────────────────────

    print_header("EXPLORATION SUMMARY")

    print(f"""
   Region Code (Da Nang): {region_code}
   Categories Found:      {len(categories)}
   Wards Found:           {len(wards)}
   Search Ad Fields:      {len(schema.get('ad_keys', []))}
   Search Params Fields:  {len(schema.get('params_keys', []))}
   Detail Fields:         {len(detail_schema.get('keys', []))}
   Detail Params Fields:  {len(detail_schema.get('params_keys', []))}
   Offset Pagination:     {schema.get('offset_pagination', 'unknown')}
   Page Pagination:       {schema.get('page_pagination', 'unknown')}

   📁 Raw JSON responses saved to: {OUTPUT_DIR.absolute()}

   Next steps:
   1. Review the saved JSON files for exact field names
   2. Update danang_realestate_spec_v2.md with confirmed values
   3. Proceed to Phase 1: build the scraper
""")

    # Save summary as JSON
    summary = {
        "explored_at": datetime.now().isoformat(),
        "region_code": region_code,
        "categories": {str(k): v for k, v in categories.items()},
        "ward_count": len(wards),
        "search_ad_keys": schema.get("ad_keys", []),
        "search_params_keys": schema.get("params_keys", []),
        "detail_keys": detail_schema.get("keys", []),
        "detail_params_keys": detail_schema.get("params_keys", []),
        "offset_pagination_works": schema.get("offset_pagination", None),
        "page_pagination_works": schema.get("page_pagination", None),
    }
    save_json(summary, "exploration_summary.json")
    print(f"   📄 Summary saved to: {OUTPUT_DIR.absolute() / 'exploration_summary.json'}")


if __name__ == "__main__":
    main()
