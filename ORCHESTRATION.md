# Orchestration (Dagster)

The pipeline is orchestrated by **Dagster**, defined in
[`src/danang_realestate/orchestration.py`](src/danang_realestate/orchestration.py).
It replaces the old cron approach (`scripts/refresh.sh` still works as a manual wrapper).

## The `daily_refresh` job

Ops run sequentially (chained via `Nothing` dependencies):

```
stop_metabase → scrape_and_load → geocode_listings → run_dbt → start_metabase
```

- **Why stop/start Metabase?** DuckDB is single-writer; while the Metabase container holds
  `data/danang.duckdb` open it blocks the pipeline from writing. The job stops it for the
  run and restarts it after. `stop_metabase`/`start_metabase` only act if a
  `docker-compose.yml` exists, and never raise.
- **Failure safety:** the `restart_metabase` failure hook brings Metabase back up if any op
  fails, so a mid-run error can't leave it stopped.
- **Re-running is the price-tracking loop:** `detect_price_changes` appends to
  `listing_price_history` (which feeds the `price_changes` mart) every time prices move.
- **Retries:** the network ops (`scrape_and_load`, `geocode_listings`) retry twice on failure.

Env overrides: `SCRAPE_TYPE` (`all`|`sale`|`rent`, default `all`), `SCRAPE_LIMIT`
(int, default `100`).

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

Run the job once, on demand:

```bash
export DAGSTER_HOME="$(pwd)/.dagster_home"
SCRAPE_LIMIT=5 uv run dagster job execute -m danang_realestate.orchestration -j daily_refresh
```

Validate definitions (CI-friendly, no daemon):

```bash
uv run dagster definitions validate -m danang_realestate.orchestration
```

## Notes / next steps

- For an always-on deployment, run the webserver + daemon as services (systemd, or a
  Docker Compose code-location like the `birdwatch-dagster-*` stack) with `DAGSTER_HOME`
  set to a persistent path.
- The cleaner long-term fix for the stop/start dance is serving Metabase from Postgres
  instead of DuckDB (pipeline writes marts to Postgres, Metabase reads concurrently) — then
  the `stop_metabase`/`start_metabase` ops can be dropped.
- dbt is run via subprocess (`uv run dbt run`). Could later switch to `dagster-dbt` to expose
  each model as an asset with lineage.
