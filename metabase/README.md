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
./fetch-driver.sh                 # downloads the driver jar + sets dir perms (see Troubleshooting)
docker compose up -d              # starts Metabase on http://localhost:3001
```

Then in the browser:
1. Open http://localhost:3001 and create the admin account.
2. Add database → **DuckDB** → Database file = `/data/danang.duckdb` → save.
   (That's the in-container path; it's mounted read-only from `../data/danang.duckdb`.)
3. Build the dashboards (see below) on the mart tables.

## ⚠️ DuckDB is single-writer — important

DuckDB allows only one writer and does not allow a writer while another process holds
the file open. While this Metabase container is running it keeps `danang.duckdb` open
(read-only), which **blocks the scrape/transform pipeline from writing it**.

Recommended workflow for refreshing data — use the wrapper, which bounces Metabase
automatically (stop → pipeline → start, only restarting if it was running):

```bash
../scripts/refresh.sh                 # run-all --type all --limit 100
SCRAPE_LIMIT=250 ../scripts/refresh.sh
WITH_RESCRAPE=1 ../scripts/refresh.sh # also recheck active listings (offline + price)
```

Or manually:

```bash
cd metabase && docker compose stop metabase     # release the file lock
cd .. && uv run danang-realestate run-all        # scrape -> geocode -> transform
cd metabase && docker compose start metabase     # serve fresh data
```

Metabase keeps all your dashboards/questions (they live in `./metabase-data`, not in
the DuckDB file), so stop/start is cheap and lossless.

### Scheduled refresh (Dagster)

Re-running the pipeline is the price-tracking loop (`detect_price_changes` appends to
`listing_price_history`, which feeds the `price_changes` mart). Orchestration is handled by
**Dagster** (`daily_refresh` job + 03:00 Asia/Ho_Chi_Minh schedule) — see
[`../ORCHESTRATION.md`](../ORCHESTRATION.md). The job stops this Metabase
container for the duration of the run and restarts it afterwards (even on failure).

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

## Troubleshooting

**DuckDB is not in the "Add database" engine list.** The driver jar is present in
`plugins/` but Metabase never loaded it. The Metabase java process runs as **uid 2000**
(`metabase`) and must be able to *write* to both `plugins/` and `metabase-data/` — it
scans/extracts driver jars in the plugins dir and writes its H2 app DB to `metabase-data`.
If either dir is owned by another uid (e.g. Docker auto-creates `metabase-data` as root)
without the write bit, Metabase logs `cannot use the plugins directory`, silently falls
back to `/tmp`, and only loads its built-in drivers — so `duckdb` never appears.

`fetch-driver.sh` now `chmod 0777`s both dirs to prevent this. To fix an already-running
container without re-running the script:

```bash
docker exec -u root danang-metabase chmod 777 /plugins /metabase-data
docker compose restart
# verify the driver registered (expect: duckdb present: True):
curl -s localhost:3001/api/session/properties | python3 -c 'import sys,json; e=json.load(sys.stdin)["engines"]; print("duckdb present:", "duckdb" in e)'
```

Also check the logs: `docker compose logs | grep -i "Loading plugins in"` should say
`/plugins`, not `/tmp`.

## Files

- `docker-compose.yml` — the Metabase service (driver + read-only data mount + persistence).
- `fetch-driver.sh` — downloads the pinned DuckDB driver jar + sets writable dir perms.
- `plugins/` — driver jar lives here (gitignored).
- `metabase-data/` — Metabase app state, dashboards, users (gitignored).
