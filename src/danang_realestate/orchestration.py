"""Dagster orchestration for the Da Nang real estate pipeline.

Replaces the old cron + scripts/refresh.sh approach. The pipeline writes the
single-writer DuckDB file, so the Metabase container (which holds it open
read-only) is stopped for the duration of the run and restarted afterwards —
including on failure, via the `restart_metabase` failure hook.

Run locally (birdwatch already uses port 3000, so use another port):

    uv sync --extra dagster
    uv run dagster dev -m danang_realestate.orchestration -p 3070

Then open http://localhost:3070 → the `daily_refresh` job runs scrape → geocode →
dbt, bracketed by Metabase stop/start. The `daily_refresh_schedule` fires at 03:00
Asia/Ho_Chi_Minh (needs the dagster daemon, which `dagster dev` runs).

Env overrides: SCRAPE_TYPE (all|sale|rent, default all), SCRAPE_LIMIT (int, default 100).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from dagster import (
    DefaultScheduleStatus,
    Definitions,
    HookContext,
    In,
    Nothing,
    Out,
    RetryPolicy,
    ScheduleDefinition,
    failure_hook,
    job,
    op,
)

from danang_realestate.db import get_connection, init_db
from danang_realestate.pipeline.geocoder import Geocoder
from danang_realestate.pipeline.loader import load_listings
from danang_realestate.scrapers.nhatot import NhaTotScraper
from danang_realestate.utils.http import SafeHTTPClient

REPO_ROOT = Path(__file__).resolve().parents[2]
METABASE_DIR = REPO_ROOT / "metabase"
DBT_DIR = REPO_ROOT / "dbt"

SCRAPE_TYPE = os.getenv("SCRAPE_TYPE", "all")
SCRAPE_LIMIT = int(os.getenv("SCRAPE_LIMIT", "100"))

# Network ops can hit transient failures; give the scrape a couple of retries.
_NET_RETRY = RetryPolicy(max_retries=2, delay=10)


def _metabase(context, action: str) -> None:
    """Best-effort `docker compose <action>` in metabase/. Never raises."""
    compose = METABASE_DIR / "docker-compose.yml"
    if not compose.exists():
        context.log.info("No metabase/docker-compose.yml; skipping '%s'.", action)
        return
    try:
        subprocess.run(
            ["docker", "compose", action],
            cwd=str(METABASE_DIR),
            check=False,
            capture_output=True,
            text=True,
        )
        context.log.info("Metabase: docker compose %s", action)
    except FileNotFoundError:
        context.log.warning("docker not found; cannot %s Metabase.", action)


@op(out=Out(Nothing))
def stop_metabase(context) -> None:
    """Release the single-writer DuckDB file so the pipeline can write it."""
    context.log.info("Stopping Metabase to free the DuckDB file...")
    _metabase(context, "stop")


@op(out=Out(Nothing), ins={"start": In(Nothing)}, retry_policy=_NET_RETRY)
def scrape_and_load(context) -> None:
    """Scrape nhatot listings and upsert them (records price history)."""
    init_db()
    client = SafeHTTPClient()
    try:
        scraper = NhaTotScraper(client)
        listings = scraper.scrape(transaction_type=SCRAPE_TYPE, limit=SCRAPE_LIMIT)
        context.log.info("Scraped %d listings (type=%s, limit=%s).", len(listings), SCRAPE_TYPE, SCRAPE_LIMIT)
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
    try:
        Geocoder(conn).geocode_pending_listings()
    finally:
        conn.close()


@op(out=Out(Nothing), ins={"start": In(Nothing)})
def run_dbt(context) -> None:
    """Rebuild the analytics marts via dbt."""
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


@op(ins={"start": In(Nothing)})
def start_metabase(context) -> None:
    """Bring Metabase back up to serve the freshly-built marts."""
    _metabase(context, "start")


@failure_hook
def restart_metabase(context: HookContext) -> None:
    """If any op fails, make sure Metabase is brought back up (best effort)."""
    context.log.warning("Op '%s' failed; restarting Metabase.", context.op.name)
    _metabase(context, "start")


@job(hooks={restart_metabase})
def daily_refresh():
    """Full refresh: stop Metabase → scrape → geocode → dbt → start Metabase."""
    stopped = stop_metabase()
    scraped = scrape_and_load(start=stopped)
    geocoded = geocode_listings(start=scraped)
    transformed = run_dbt(start=geocoded)
    start_metabase(start=transformed)


daily_refresh_schedule = ScheduleDefinition(
    name="daily_refresh_schedule",
    job=daily_refresh,
    cron_schedule="0 3 * * *",
    execution_timezone="Asia/Ho_Chi_Minh",
    # Start enabled, so the daemon runs it without toggling it on in the UI first.
    default_status=DefaultScheduleStatus.RUNNING,
)

defs = Definitions(jobs=[daily_refresh], schedules=[daily_refresh_schedule])
