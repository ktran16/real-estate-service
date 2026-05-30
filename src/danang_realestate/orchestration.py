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
  PG_HOST, PG_PORT, PG_DB, PG_USER, PG_SCHEMA and the secret PG_PASSWORD (or
  POSTGRES_PASSWORD) — all read via `config.settings` (single source of truth). The
  password has no baked-in default; set it in an untracked .env (see .env.example).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from dagster import (
    DefaultScheduleStatus,
    DefaultSensorStatus,
    Definitions,
    In,
    Nothing,
    Out,
    RetryPolicy,
    RunFailureSensorContext,
    ScheduleDefinition,
    job,
    op,
    run_failure_sensor,
)

from danang_realestate.alerting import post_slack
from danang_realestate.config import settings
from danang_realestate.db import get_connection, init_db
from danang_realestate.pipeline.geocoder import Geocoder
from danang_realestate.pipeline.loader import load_listings
from danang_realestate.scrapers import get_scraper
from danang_realestate.utils.http import SafeHTTPClient
from danang_realestate.validation.schema_validator import validate_schema

REPO_ROOT = Path(__file__).resolve().parents[2]
DBT_DIR = REPO_ROOT / "dbt"

# SCRAPE_SOURCE is the one knob not yet in Settings; keep the env read here.
SCRAPE_SOURCE = os.getenv("SCRAPE_SOURCE", "nhatot")
SCRAPE_TYPE = os.getenv("SCRAPE_TYPE", "all")
SCRAPE_LIMIT = int(os.getenv("SCRAPE_LIMIT", "100"))

# Postgres serving DB (the copy Metabase reads). Read from config.settings, which sources
# PG_HOST/PG_PORT/PG_DB/PG_USER/PG_SCHEMA and the secret PG_PASSWORD from env/.env.
# Inside the Dagster container the compose sets PG_HOST=postgres PG_PORT=5432.
PG_HOST = settings.pg_host
PG_PORT = settings.pg_port
PG_DB = settings.pg_db
PG_USER = settings.pg_user
PG_PASSWORD = settings.pg_password
PG_SCHEMA = settings.pg_schema

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
    """Publish the dbt marts to the Postgres serving DB (what Metabase reads) atomically.

    Uses DuckDB's native `postgres` extension to ATTACH Postgres. Each mart is written to a
    `<mart>__staging` table first; then ALL marts are swapped into place inside a single
    native Postgres transaction (DROP old + RENAME staging → final). This removes the brief
    empty-table window the previous drop-then-recreate had: Metabase always reads either the
    full previous marts or the full new ones, never an empty or half-published table. Metabase
    never opens the DuckDB file, so no single-writer dance.
    """
    if not PG_PASSWORD:
        raise RuntimeError(
            "Postgres password is not set. Define PG_PASSWORD (or POSTGRES_PASSWORD) in "
            "an untracked .env — there is no baked-in default. See .env.example."
        )
    conn = get_connection()
    try:
        conn.execute("INSTALL postgres; LOAD postgres;")
        dsn = f"host={PG_HOST} port={PG_PORT} dbname={PG_DB} user={PG_USER} password={PG_PASSWORD}"
        conn.execute(f"ATTACH '{dsn}' AS pg (TYPE postgres)")
        try:
            # 1) Build every present mart into a fresh staging table. If anything here
            #    fails, the live tables are untouched (we haven't swapped yet).
            published = []
            swap_stmts = []
            for mart in MARTS:
                present = conn.execute(
                    "SELECT count(*) FROM duckdb_tables() "
                    "WHERE database_name = current_database() AND table_name = ?",
                    [mart],
                ).fetchone()[0]
                if not present:
                    context.log.info("Mart '%s' not found in DuckDB; skipping.", mart)
                    continue
                staging = f"{mart}__staging"
                conn.execute(f'DROP TABLE IF EXISTS pg.{PG_SCHEMA}."{staging}"')
                conn.execute(
                    f'CREATE TABLE pg.{PG_SCHEMA}."{staging}" AS SELECT * FROM "{mart}"'
                )
                n = conn.execute(
                    f'SELECT count(*) FROM pg.{PG_SCHEMA}."{staging}"'
                ).fetchone()[0]
                published.append(f"{mart}={n}")
                swap_stmts.append(f'DROP TABLE IF EXISTS {PG_SCHEMA}."{mart}";')
                swap_stmts.append(
                    f'ALTER TABLE {PG_SCHEMA}."{staging}" RENAME TO "{mart}";'
                )

            # 2) Swap all staging tables into place in ONE Postgres transaction, executed
            #    natively on the server (postgres_execute) so the cutover is atomic.
            if swap_stmts:
                swap_sql = "BEGIN;\n" + "\n".join(swap_stmts) + "\nCOMMIT;"
                conn.execute("CALL postgres_execute('pg', ?)", [swap_sql])
        finally:
            conn.execute("DETACH pg")
        context.log.info(
            "Published marts to Postgres %s:%s (atomic swap) → %s",
            PG_HOST, PG_PORT, ", ".join(published) or "(none)",
        )
    finally:
        conn.close()


@op(retry_policy=_NET_RETRY)
def check_api_schema(context) -> None:
    """Detect nhatot/chotot API schema drift against the NhaTotAd model.

    Raises on drift so the run fails — `pipeline_failure_alert` then notifies. This is the
    early-warning that a silent API change has broken (or is about to break) scraping.
    """
    client = SafeHTTPClient()
    try:
        report = validate_schema(client)
    finally:
        client.close()
    report.print_summary()
    if report.has_drift():
        raise RuntimeError(
            "nhatot/chotot API schema drift detected. "
            f"Extra fields: {sorted(report.extra_in_api)}. "
            f"Missing critical fields: {sorted(report.missing_in_api & {'ad_id', 'list_id', 'account_id', 'price', 'size', 'type'})}."
        )
    context.log.info("No critical API schema drift.")


@job
def daily_refresh():
    """Full refresh: scrape → geocode → dbt marts → publish to Postgres serving DB."""
    scraped = scrape_and_load()
    geocoded = geocode_listings(start=scraped)
    transformed = run_dbt(start=geocoded)
    publish_to_postgres(start=transformed)


@job
def schema_drift_check():
    """Standalone API-schema-drift check (fails loudly so the alert sensor fires)."""
    check_api_schema()


daily_refresh_schedule = ScheduleDefinition(
    name="daily_refresh_schedule",
    job=daily_refresh,
    cron_schedule="0 3 * * *",
    execution_timezone="Asia/Ho_Chi_Minh",
    # Start enabled, so the daemon runs it without toggling it on in the UI first.
    default_status=DefaultScheduleStatus.RUNNING,
)

schema_drift_schedule = ScheduleDefinition(
    name="schema_drift_schedule",
    job=schema_drift_check,
    # Weekly, Monday 04:00 (after Monday's daily refresh).
    cron_schedule="0 4 * * 1",
    execution_timezone="Asia/Ho_Chi_Minh",
    default_status=DefaultScheduleStatus.RUNNING,
)


@run_failure_sensor(
    monitored_jobs=[daily_refresh, schema_drift_check],
    default_status=DefaultSensorStatus.RUNNING,
)
def pipeline_failure_alert(context: RunFailureSensorContext) -> None:
    """Post a Slack alert when daily_refresh or the schema-drift check fails.

    No-ops if SLACK_WEBHOOK_URL is unset (see alerting.post_slack), so the sensor is safe
    to leave enabled even without alerting configured.
    """
    run = context.dagster_run
    error = context.failure_event.message or "(no error message)"
    post_slack(
        f":rotating_light: *{run.job_name}* failed (run {run.run_id[:8]}).\n{error}"
    )


defs = Definitions(
    jobs=[daily_refresh, schema_drift_check],
    schedules=[daily_refresh_schedule, schema_drift_schedule],
    sensors=[pipeline_failure_alert],
)
