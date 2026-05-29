"""Dagster orchestration for the Da Nang real estate pipeline.

Architecture: DuckDB is the pipeline's working store (raw listings + dbt marts).
A separate Postgres database holds a *serving copy* of the marts that Metabase reads
(Metabase's built-in Postgres driver — no jar, always-on, concurrent with pipeline
writes). Because Metabase never opens the DuckDB file, there is no single-writer
stop/start dance: the job just scrapes → geocodes → builds marts → publishes to Postgres.

Run locally (birdwatch already uses port 3000, so use another port):

    uv sync --extra dagster
    uv run dagster dev -m danang_realestate.orchestration -p 3070

The `daily_refresh_schedule` fires at 03:00 Asia/Ho_Chi_Minh (needs the dagster daemon,
which `dagster dev` runs). Marts are published to Postgres via DuckDB's native `postgres`
extension (no extra Python dependency).

Env overrides:
  SCRAPE_TYPE (all|sale|rent, default all), SCRAPE_LIMIT (int, default 100)
  PG_HOST (default localhost), PG_PORT (5432), PG_DB (danang), PG_USER (danang),
  PG_PASSWORD (danang), PG_SCHEMA (public)
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from dagster import (
    DefaultScheduleStatus,
    Definitions,
    In,
    Nothing,
    Out,
    RetryPolicy,
    ScheduleDefinition,
    job,
    op,
)

from danang_realestate.db import get_connection, init_db
from danang_realestate.pipeline.geocoder import Geocoder
from danang_realestate.pipeline.loader import load_listings
from danang_realestate.scrapers import get_scraper
from danang_realestate.utils.http import SafeHTTPClient

REPO_ROOT = Path(__file__).resolve().parents[2]
DBT_DIR = REPO_ROOT / "dbt"

SCRAPE_SOURCE = os.getenv("SCRAPE_SOURCE", "nhatot")
SCRAPE_TYPE = os.getenv("SCRAPE_TYPE", "all")
SCRAPE_LIMIT = int(os.getenv("SCRAPE_LIMIT", "100"))

# Postgres serving DB (the copy Metabase reads). Defaults suit host runs against the
# container published on 5433; inside the Dagster container set PG_HOST=postgres PG_PORT=5432.
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = os.getenv("PG_PORT", "5432")
PG_DB = os.getenv("PG_DB", "danang")
PG_USER = os.getenv("PG_USER", "danang")
PG_PASSWORD = os.getenv("PG_PASSWORD", "danang")
PG_SCHEMA = os.getenv("PG_SCHEMA", "public")

# The dbt marts to publish to Postgres for Metabase to serve.
MARTS = ["listings", "price_by_district", "price_trend", "price_changes", "broker_listings"]

# Network/DB ops can hit transient failures; give them a couple of retries.
_NET_RETRY = RetryPolicy(max_retries=2, delay=10)


@op(out=Out(Nothing), retry_policy=_NET_RETRY)
def scrape_and_load(context) -> None:
    """Scrape nhatot listings and upsert them (records price history)."""
    init_db()
    client = SafeHTTPClient()
    try:
        scraper = get_scraper(SCRAPE_SOURCE, client)
        listings = scraper.scrape(transaction_type=SCRAPE_TYPE, limit=SCRAPE_LIMIT)
        context.log.info(
            "Scraped %d listings (source=%s, type=%s, limit=%s).",
            len(listings), SCRAPE_SOURCE, SCRAPE_TYPE, SCRAPE_LIMIT,
        )
        conn = get_connection()
        try:
            load_listings(conn, listings)
        finally:
            conn.close()
    finally:
        client.close()


@op(out=Out(Nothing), ins={"start": In(Nothing)}, retry_policy=_NET_RETRY)
def geocode_listings(context) -> None:
    """Geocode any listings missing coordinates (cache → Nominatim → centroid)."""
    init_db()
    conn = get_connection()
    geocoder = Geocoder(conn)
    try:
        geocoder.geocode_pending_listings()
    finally:
        geocoder.close()
        conn.close()


@op(out=Out(Nothing), ins={"start": In(Nothing)})
def run_dbt(context) -> None:
    """Rebuild the analytics marts via dbt (in DuckDB)."""
    result = subprocess.run(
        ["uv", "run", "dbt", "run", "--profiles-dir", "."],
        cwd=str(DBT_DIR),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.stdout:
        context.log.info(result.stdout)
    if result.returncode != 0:
        context.log.error(result.stderr)
        raise RuntimeError(f"dbt run failed (exit {result.returncode})")


@op(ins={"start": In(Nothing)}, retry_policy=_NET_RETRY)
def publish_to_postgres(context) -> None:
    """Copy the dbt marts from DuckDB into the Postgres serving DB (what Metabase reads).

    Uses DuckDB's native `postgres` extension to ATTACH Postgres and rewrite each mart
    table, so Metabase serves a fresh copy without ever opening the DuckDB file.
    """
    conn = get_connection()
    try:
        conn.execute("INSTALL postgres; LOAD postgres;")
        dsn = f"host={PG_HOST} port={PG_PORT} dbname={PG_DB} user={PG_USER} password={PG_PASSWORD}"
        conn.execute(f"ATTACH '{dsn}' AS pg (TYPE postgres)")
        try:
            published = []
            for mart in MARTS:
                present = conn.execute(
                    "SELECT count(*) FROM duckdb_tables() "
                    "WHERE database_name = current_database() AND table_name = ?",
                    [mart],
                ).fetchone()[0]
                if not present:
                    context.log.info("Mart '%s' not found in DuckDB; skipping.", mart)
                    continue
                # CREATE OR REPLACE isn't supported on attached Postgres; drop + create.
                conn.execute(f'DROP TABLE IF EXISTS pg.{PG_SCHEMA}."{mart}"')
                conn.execute(f'CREATE TABLE pg.{PG_SCHEMA}."{mart}" AS SELECT * FROM "{mart}"')
                n = conn.execute(f'SELECT count(*) FROM pg.{PG_SCHEMA}."{mart}"').fetchone()[0]
                published.append(f"{mart}={n}")
        finally:
            conn.execute("DETACH pg")
        context.log.info("Published marts to Postgres %s:%s → %s", PG_HOST, PG_PORT, ", ".join(published) or "(none)")
    finally:
        conn.close()


@job
def daily_refresh():
    """Full refresh: scrape → geocode → dbt marts → publish to Postgres serving DB."""
    scraped = scrape_and_load()
    geocoded = geocode_listings(start=scraped)
    transformed = run_dbt(start=geocoded)
    publish_to_postgres(start=transformed)


daily_refresh_schedule = ScheduleDefinition(
    name="daily_refresh_schedule",
    job=daily_refresh,
    cron_schedule="0 3 * * *",
    execution_timezone="Asia/Ho_Chi_Minh",
    # Start enabled, so the daemon runs it without toggling it on in the UI first.
    default_status=DefaultScheduleStatus.RUNNING,
)

defs = Definitions(jobs=[daily_refresh], schedules=[daily_refresh_schedule])
