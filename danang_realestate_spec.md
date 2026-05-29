# Da Nang Real Estate Analytics — Project Spec

## Overview

A self-hosted real estate analytics platform for the Da Nang market. Scrapes listing data from multiple Vietnamese property portals, normalizes and stores it locally, and surfaces insights via a Metabase dashboard with map-based price visualization.

---

## Goals

- Track property listing prices across Da Nang by district and neighborhood
- Identify price trends and high-growth zones over time
- Visualize price distribution geographically (map view)
- Build a clean, reusable dataset for future ML/analysis work

---

## Infrastructure

| Component | Choice | Notes |
|-----------|--------|-------|
| Orchestration | Dagster | Already running as systemd service on OptiPlex 3070 |
| Transformation | dbt | Already configured with Redshift; use DuckDB locally for this project |
| Storage | DuckDB | Lightweight, file-based, no server needed for homelab |
| Dashboard | Metabase | Already running; connect via DuckDB or export to PostgreSQL |
| Language | Python 3.11+ | Package manager: `uv` |
| Remote access | Tailscale | Already configured |

> **Note:** Use DuckDB (not Redshift) to keep this project isolated from Birdwatch infra.

---

## Data Sources

Priority order based on data richness and scrapeability:

| Priority | Site | Method | Notes |
|----------|------|--------|-------|
| 1 | nhatot.com | Mobile API (reverse-engineered) | Most open, JSON responses |
| 2 | batdongsan.com.vn | HTML scraping + pagination | Richest data, heavier anti-bot |
| 3 | mogi.vn | HTML scraping | Moderate protection |
| 4 | alonhadat.com.vn | HTML scraping | Easier to scrape |

### Target fields per listing

```
listing_id          -- unique ID from source
source              -- nhatot | batdongsan | mogi | alonhadat
url                 -- original listing URL
title               -- listing title
property_type       -- apartment | house | land | villa | shophouse
transaction_type    -- sale | rent
price               -- numeric (VND)
price_per_sqm       -- derived: price / area
area_sqm            -- floor area
bedrooms            -- nullable
bathrooms           -- nullable
address_raw         -- raw text address
district            -- extracted/normalized
ward                -- extracted/normalized
lat                 -- geocoded latitude
lng                 -- geocoded longitude
posted_at           -- listing post date
scraped_at          -- when we collected it
is_active           -- bool, updated on re-scrape
```

---

## Project Structure

```
danang-realestate/
├── pyproject.toml
├── uv.lock
├── .env                        # API keys, configs
├── data/
│   └── danang.duckdb           # main database
├── scrapers/
│   ├── base.py                 # BaseScaper abstract class
│   ├── nhatot.py
│   ├── batdongsan.py
│   ├── mogi.py
│   └── alonhadat.py
├── pipeline/
│   ├── geocoder.py             # address → lat/lng
│   ├── normalizer.py           # clean/normalize raw data
│   └── loader.py               # write to DuckDB
├── dagster_jobs/
│   ├── __init__.py
│   ├── scrape_job.py           # daily scrape job
│   └── schedules.py
├── dbt/
│   ├── dbt_project.yml
│   ├── models/
│   │   ├── staging/
│   │   │   └── stg_listings.sql
│   │   ├── intermediate/
│   │   │   └── int_listings_geocoded.sql
│   │   └── marts/
│   │       ├── listings.sql         -- clean final table
│   │       ├── price_by_district.sql
│   │       └── price_trend.sql
└── README.md
```

---

## Data Pipeline

```
[Scrapers] → raw_listings (DuckDB)
           → [Normalizer] → [Geocoder] → staging layer
           → [dbt] → mart tables
           → [Metabase] → dashboards
```

### Scrape schedule (Dagster)
- Full scrape: daily at 2:00 AM ICT
- Re-check active listings: every 3 days (detect price changes / delisted)

---

## Geocoding Strategy

Vietnamese addresses are messy. Use a tiered approach:

1. **Goong Maps API** (Vietnamese geocoder, free tier: 10k req/month) — primary
2. **Nominatim (OpenStreetMap)** — free fallback, slower, less accurate for VN
3. **Manual district lookup table** — if both fail, assign district centroid from a static `district_centroids.csv`

Cache all geocoded results in a `geocode_cache` table to avoid re-querying.

---

## Anti-Scraping Mitigation

- Rotate `User-Agent` headers
- Add randomized delays between requests (2–8 seconds)
- Use `httpx` with HTTP/2 support (better fingerprint for some sites)
- Respect `robots.txt` where applicable
- For batdongsan.com.vn: use Playwright for JS-rendered pages if needed
- Rate limit: max 1 request/3 seconds per domain

---

## dbt Models

### Staging
- `stg_listings` — dedup raw listings, cast types, basic null handling

### Intermediate
- `int_listings_geocoded` — join with geocode cache, add lat/lng

### Marts
- `listings` — final clean table, one row per listing
- `price_by_district` — avg/median price and price_per_sqm by district + property_type
- `price_trend` — weekly median price per district, for trend charts

---

## Metabase Dashboards

### Dashboard 1: Market Overview
- Total active listings by type (bar chart)
- Median price by district (table + bar)
- Price distribution histogram

### Dashboard 2: Map View
- Pin map: each listing as a dot, color = price_per_sqm bucket
- Heatmap layer: price density by area (if Metabase version supports it)
- Filter: property type, transaction type, date range

### Dashboard 3: Trend Analysis
- Weekly median price per district (line chart)
- New listings volume over time
- Price change alerts (listings re-scraped with different price)

> **Map note:** Metabase's native pin map requires `latitude` and `longitude` columns — these must be present in the mart table. For richer map UX, consider exporting to Kepler.gl as a secondary view.

---

## Dependencies

```toml
[project]
name = "danang-realestate"
requires-python = ">=3.11"

dependencies = [
    "httpx[http2]",
    "playwright",
    "beautifulsoup4",
    "lxml",
    "duckdb",
    "dagster",
    "dagster-duckdb",
    "dbt-duckdb",
    "pydantic",
    "tenacity",          # retry logic
    "python-dotenv",
    "pandas",
    "rich",              # CLI progress
]
```

---

## Environment Variables (.env)

```env
GOONG_API_KEY=...
DAGSTER_HOME=/path/to/dagster_home
DUCKDB_PATH=./data/danang.duckdb
SCRAPE_DELAY_MIN=2
SCRAPE_DELAY_MAX=8
```

---

## Milestones

| Phase | Deliverable |
|-------|-------------|
| 1 | nhatot.com scraper working, data into DuckDB |
| 2 | Geocoder pipeline + district normalization |
| 3 | dbt models + Metabase connected |
| 4 | Map dashboard live |
| 5 | Add batdongsan + mogi scrapers |
| 6 | Dagster scheduling + daily runs |
| 7 | Price trend tracking (re-scrape + diff) |

---

## Out of Scope (v1)

- ML price prediction model
- Public-facing web app
- National coverage (Da Nang only for now)
- Real-time alerts / notifications
