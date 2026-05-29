# Self-hosted Metabase

Dashboards for the Da Nang real estate marts, served from the project's DuckDB file.

## Versions (must match)

| Component        | Version    | Why                                                |
|------------------|------------|----------------------------------------------------|
| Metabase image   | `v0.59.0`  | DuckDB driver `1.5.3.0` targets Metabase 59        |
| DuckDB driver    | `1.5.3.0`  | Bundles DuckDB 1.5.3                                |
| DuckDB file      | 1.5.3      | Written by the pipeline's duckdb lib (1.5.3)        |

If you bump the Metabase image, keep it on a `v0.59.x` tag (or bump the driver to a
release built for that Metabase major version).

## First-time setup

```bash
cd metabase
./fetch-driver.sh                 # downloads the driver jar into ./plugins
docker compose up -d              # starts Metabase on http://localhost:3000
```

Then in the browser:
1. Open http://localhost:3000 and create the admin account.
2. Add database → **DuckDB** → Database file = `/data/danang.duckdb` → save.
   (That's the in-container path; it's mounted read-only from `../data/danang.duckdb`.)
3. Build the dashboards (see below) on the mart tables.

## ⚠️ DuckDB is single-writer — important

DuckDB allows only one writer and does not allow a writer while another process holds
the file open. While this Metabase container is running it keeps `danang.duckdb` open
(read-only), which **blocks the scrape/transform pipeline from writing it**.

Recommended workflow for refreshing data:

```bash
cd metabase && docker compose stop metabase     # release the file lock
cd .. && uv run danang-realestate run-all        # scrape -> geocode -> transform
cd metabase && docker compose start metabase     # serve fresh data
```

Metabase keeps all your dashboards/questions (they live in `./metabase-data`, not in
the DuckDB file), so stop/start is cheap and lossless.

If you want Metabase always-on with concurrent refreshes, the clean upgrade is to serve
from Postgres instead of DuckDB (pipeline writes marts to Postgres, Metabase reads it) —
ask and we can add that.

## Dashboards (from the spec) → mart tables

- **Market Overview** — `price_by_district` (median price by district, counts),
  `listings` (type breakdown, legal-doc pie, price histogram).
- **Map View** — `listings` (has `lat`/`lng`; Metabase pin map, color by
  `price_per_sqm`; filters on property/transaction type, legal status).
- **Trend Analysis** — `price_trend` (weekly median price/district line chart, volume),
  `price_changes` (re-scrape price-change alerts).

## Files

- `docker-compose.yml` — the Metabase service (driver + read-only data mount + persistence).
- `fetch-driver.sh` — downloads the pinned DuckDB driver jar.
- `plugins/` — driver jar lives here (gitignored).
- `metabase-data/` — Metabase app state, dashboards, users (gitignored).
