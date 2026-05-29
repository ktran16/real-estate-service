# Orchestration (Dagster)

The pipeline is orchestrated by **Dagster**, defined in
[`src/danang_realestate/orchestration.py`](src/danang_realestate/orchestration.py).
It replaces the old cron approach (`scripts/refresh.sh` still works as a manual wrapper).

## Architecture

DuckDB is the pipeline's working store (raw listings + dbt marts). A separate **Postgres**
database holds a serving copy of the marts that Metabase reads. Because Metabase never opens
the DuckDB file, there's no single-writer stop/start dance — Metabase stays up during runs.

```
scrape → DuckDB(raw) → geocode → dbt marts (DuckDB) → publish_to_postgres → Postgres → Metabase
```

## The `daily_refresh` job

Ops run sequentially (chained via `Nothing` dependencies):

```
scrape_and_load → geocode_listings → run_dbt → publish_to_postgres
```

- **`publish_to_postgres`** copies the marts (`listings`, `price_by_district`, `price_trend`,
  `price_changes`, `broker_listings`) from DuckDB into Postgres using DuckDB's native
  `postgres` extension (`ATTACH ... TYPE postgres`, then drop+create each table). No extra
  Python dependency. Connection via `PG_*` env (see below).
- **Re-running is the price-tracking loop:** `detect_price_changes` appends to
  `listing_price_history` (which feeds the `price_changes` mart) every time prices move.
- **Retries:** the network/DB ops retry twice on failure.

Env overrides: `SCRAPE_TYPE` (`all`|`sale`|`rent`, default `all`), `SCRAPE_LIMIT`
(int, default `100`); `PG_HOST` (default `localhost`; the container sets `postgres`),
`PG_PORT` (`5432`; host-published `5433`), `PG_DB`/`PG_USER`/`PG_PASSWORD` (all `danang`),
`PG_SCHEMA` (`public`).

## Schedule

`daily_refresh_schedule` — cron `0 3 * * *`, timezone `Asia/Ho_Chi_Minh`, and starts
**enabled** (`DefaultScheduleStatus.RUNNING`), so the daemon runs it without toggling.

## Run it locally

```bash
uv sync --extra dagster
export DAGSTER_HOME="$(pwd)/.dagster_home"      # persists run history + schedule state
uv run dagster dev -m danang_realestate.orchestration -p 3070
```

Open http://localhost:3070. Port 3070 avoids the existing `birdwatch-dagster` on 3000.
`dagster dev` runs both the webserver and the daemon (the daemon is what fires schedules).

For host runs against the containerized Postgres, point at the published port:

```bash
export DAGSTER_HOME="$(pwd)/.dagster_home"
PG_HOST=localhost PG_PORT=5433 \
SCRAPE_LIMIT=5 uv run dagster job execute -m danang_realestate.orchestration -j daily_refresh
```

Validate definitions (CI-friendly, no daemon):

```bash
uv run dagster definitions validate -m danang_realestate.orchestration
```

## Always-on (Docker)

The `orchestration/` dir holds a Docker stack (webserver + daemon) that runs the schedule
24/7. It shares the `danang-net` network with the Metabase/Postgres stack, so the job
publishes to `postgres:5432` directly.

```bash
docker network create danang-net           # once (shared with the metabase stack)
cd metabase && docker compose up -d         # Postgres + Metabase
cd ../orchestration && docker compose up -d --build   # Dagster webserver (:3070) + daemon
```

Code is baked into the image (`COPY src` + `uv sync`); **rebuild after editing
`orchestration.py`**. `dbt/` and `data/` are bind-mounted (live models + DuckDB file).
Run the job inside the container:

```bash
cd orchestration
docker compose exec -e SCRAPE_LIMIT=5 dagster-daemon \
  uv run dagster job execute -m danang_realestate.orchestration -j daily_refresh
```

## Notes / next steps

- dbt is run via subprocess (`uv run dbt run`). Could later switch to `dagster-dbt` to expose
  each model as an asset with lineage.
- The `publish_to_postgres` op rewrites whole mart tables each run (drop + create). Fine at
  this scale; switch to incremental/upsert if the marts grow large.
