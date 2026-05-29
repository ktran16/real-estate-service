# Self-hosted Metabase + Postgres serving DB

Dashboards for the Da Nang real estate marts. Metabase reads a **Postgres serving
database** (`danang-postgres`) that the Dagster pipeline publishes the dbt marts into
after each run. Metabase never opens the DuckDB file, so it stays up concurrently with
pipeline writes — no single-writer stop/start dance.

```
pipeline → DuckDB (raw + dbt marts) → publish_to_postgres → Postgres → Metabase
```

## First-time setup

```bash
cd metabase
docker network create danang-net        # once; shared with the orchestration stack
docker compose up -d                     # starts Postgres (5433) + Metabase (3001)
```

Then in the browser:
1. Open http://localhost:3001 and create the admin account.
2. **Add database → PostgreSQL**:
   - Host `postgres`, Port `5432`, Database `danang`, User `danang`, Password `danang`.
   - (Metabase reaches Postgres by service name over the `danang-net` network.)
3. Populate the marts: run the Dagster `daily_refresh` job (see
   [`../ORCHESTRATION.md`](../ORCHESTRATION.md)) or wait for the 03:00 schedule.
4. Build the dashboards (below) on the Postgres mart tables.

> Postgres data persists in the `pgdata` volume; Metabase's own state (dashboards, users)
> in `./metabase-data`. Both survive `docker compose down`/`up`.

## Refreshing data

Orchestrated by **Dagster** (`daily_refresh` job + 03:00 Asia/Ho_Chi_Minh schedule) — see
[`../ORCHESTRATION.md`](../ORCHESTRATION.md). Each run scrapes → geocodes → builds the dbt
marts in DuckDB → **publishes them to Postgres**. Re-running is the price-tracking loop
(`detect_price_changes` appends to `listing_price_history`, which feeds the `price_changes`
mart). No need to stop Metabase.

`../scripts/refresh.sh` remains as a manual host-side wrapper for ad-hoc runs.

## Dashboards (from the spec) → mart tables

- **Market Overview** — `price_by_district` (median price by district, counts),
  `listings` (type breakdown, legal-doc pie, price histogram).
- **Map View** — `listings` (has `lat`/`lng`; Metabase pin map, color by
  `price_per_sqm`; filters on property/transaction type, legal status).
- **Trend Analysis** — `price_trend` (weekly median price/district line chart, volume),
  `price_changes` (re-scrape price-change alerts).

## Files

- `docker-compose.yml` — Postgres serving DB + Metabase (both on `danang-net`).
- `metabase-data/` — Metabase app state, dashboards, users (gitignored).
- `Makefile` — container lifecycle helpers (`make help`).
- `fetch-driver.sh`, `plugins/` — **legacy** DuckDB driver (from when Metabase read DuckDB
  directly). Not used by the Postgres-serving setup; kept for reference / ad-hoc DuckDB use.

## Legacy: serving directly from DuckDB

Earlier this stack mounted `../data/danang.duckdb` read-only and used the MotherDuck DuckDB
driver, which made DuckDB single-writer-block the pipeline (hence the stop/start workflow).
That driver had a gotcha: the Metabase java process (uid 2000) needs write access to
`plugins/` + `metabase-data/`, else it falls back to `/tmp` and never loads the jar
(`fetch-driver.sh` `chmod 0777`s both dirs to fix this). The Postgres-serving setup above
supersedes all of that.
