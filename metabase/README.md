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
   - Host `postgres`, Port `5432`, Database `danang`, User `danang`, Password = your
     `POSTGRES_PASSWORD`. Name it **`Da Nang Marts`** (the dashboards-as-code default).
   - (Metabase reaches Postgres by service name over the `danang-net` network.)
3. Populate the marts: run the Dagster `daily_refresh` job (see
   [`../ORCHESTRATION.md`](../ORCHESTRATION.md)) or wait for the 03:00 schedule.
4. **Provision the dashboards from code** (below) — no hand-building in the UI.

> Postgres data persists in the `pgdata` volume; Metabase's own state (dashboards, users)
> in `./metabase-data`. Both survive `docker compose down`/`up`.

## Refreshing data

Orchestrated by **Dagster** (`daily_refresh` job + 03:00 Asia/Ho_Chi_Minh schedule) — see
[`../ORCHESTRATION.md`](../ORCHESTRATION.md). Each run scrapes → geocodes → builds the dbt
marts in DuckDB → **publishes them to Postgres**. Re-running is the price-tracking loop
(`detect_price_changes` appends to `listing_price_history`, which feeds the `price_changes`
mart). No need to stop Metabase.

`../scripts/refresh.sh` remains as a manual host-side wrapper for ad-hoc runs.

## Dashboards as code

The three dashboards are **version-controlled** as YAML specs under `dashboards/` and applied
via the Metabase REST API by `provision.py` — so they never have to be hand-built in the UI and
stay reproducible across rebuilds. Provisioning is **idempotent** (cards/dashboards are matched
by name within the `Da Nang Real Estate` collection and updated in place).

```bash
# Authenticate with your own admin creds or an API key (never committed):
export MB_URL=http://localhost:3001
export MB_USERNAME=you@example.com MB_PASSWORD=...      # or: export MB_API_KEY=...

uv run --extra metabase python provision.py validate    # check the specs parse (no server)
uv run --extra metabase python provision.py provision   # create/update the dashboards
uv run --extra metabase python provision.py provision --create-database   # also add the PG conn
uv run --extra metabase python provision.py export      # pull live dashboards back into YAML
```

Or via the Makefile: `make dashboards`, `make dashboards-export`, `make dashboards-validate`.

Specs (`dashboards/config.yml` lists them; each is native-SQL cards over the published marts):
- **Market Overview** — supply + price levels (`price_by_district`, `price_per_sqm_by_ward`,
  `listings`).
- **Trends & Activity** — `price_trend`, `listing_velocity`, `listing_days_on_market`,
  `price_changes`.
- **Brokers & Deals** — `broker_concentration`, `broker_listings`, `deals`.

Edit a spec → re-run `provision` to apply; or change in the UI → `export` to capture it back.

## Files

- `docker-compose.yml` — Postgres serving DB + Metabase (both on `danang-net`).
- `dashboards/` — version-controlled dashboard specs (config + one YAML per dashboard).
- `provision.py` — dashboards-as-code provision/export tool (Metabase REST API).
- `metabase-data/` — Metabase app state, dashboards, users (gitignored).
- `Makefile` — container lifecycle + dashboard helpers (`make help`).
- `fetch-driver.sh`, `plugins/` — **legacy** DuckDB driver (from when Metabase read DuckDB
  directly). Not used by the Postgres-serving setup; kept for reference / ad-hoc DuckDB use.

## Legacy: serving directly from DuckDB

Earlier this stack mounted `../data/danang.duckdb` read-only and used the MotherDuck DuckDB
driver, which made DuckDB single-writer-block the pipeline (hence the stop/start workflow).
That driver had a gotcha: the Metabase java process (uid 2000) needs write access to
`plugins/` + `metabase-data/`, else it falls back to `/tmp` and never loads the jar
(`fetch-driver.sh` `chmod 0777`s both dirs to fix this). The Postgres-serving setup above
supersedes all of that.
