"""Dagster orchestration for the Da Nang real estate pipeline.

Architecture: DuckDB is the pipeline's working store (raw listings + dbt marts). A separate
Postgres database holds a *serving copy* of the marts that Metabase reads (Metabase's built-in
Postgres driver — no jar, always-on). Because Metabase never opens the DuckDB file there is no
single-writer dance: jobs scrape → geocode → build marts → publish to Postgres.

The transform layer is modelled with **dagster-dbt**: each dbt model is its own asset, so the
daily pipeline is an asset graph

    scraped_listings → geocoded_raw (raw/* source tables) → dbt models → published_marts

with per-model lineage and dbt test results surfaced as asset checks in the Dagster UI. The
secondary op-based jobs (weekly_maintenance, schema_drift_check) reuse the same helpers and
invoke dbt through `DbtCliResource` (no subprocess).

Run locally (birdwatch already uses port 3000, so use another port):

    uv sync --extra dagster
    uv run dagster dev -m danang_realestate.orchestration -p 3070

Env overrides:
  SCRAPE_TYPE (all|sale|rent, default all), SCRAPE_LIMIT (int, default 100)
  PG_HOST, PG_PORT, PG_DB, PG_USER, PG_SCHEMA and the secret PG_PASSWORD (or
  POSTGRES_PASSWORD) — all read via `config.settings` (single source of truth). The password
  has no baked-in default; set it in an untracked .env (see .env.example).
"""
import os
from pathlib import Path

from dagster import (
    AssetExecutionContext,
    AssetKey,
    AssetSelection,
    AssetSpec,
    DagsterRunStatus,
    DefaultScheduleStatus,
    DefaultSensorStatus,
    Definitions,
    In,
    MaterializeResult,
    Nothing,
    Out,
    RetryPolicy,
    RunFailureSensorContext,
    RunStatusSensorContext,
    ScheduleDefinition,
    asset,
    define_asset_job,
    job,
    multi_asset,
    op,
    run_failure_sensor,
    run_status_sensor,
)
from dagster_dbt import DbtCliResource, DbtProject, dbt_assets

from danang_realestate.alerting import post_slack
from danang_realestate.config import settings
from danang_realestate.db import get_connection, init_db
from danang_realestate.pipeline.backup import backup_duckdb
from danang_realestate.pipeline.geocoder import Geocoder
from danang_realestate.pipeline.loader import load_listings
from danang_realestate.pipeline.observability import detect_row_drops, record_mart_counts
from danang_realestate.pipeline.rescraper import rescrape_active_listings
from danang_realestate.scrapers import get_scraper
from danang_realestate.scrapers.nhatot import NhaTotScraper
from danang_realestate.utils.http import SafeHTTPClient
from danang_realestate.validation.schema_validator import validate_schema

REPO_ROOT = Path(__file__).resolve().parents[2]
DBT_DIR = REPO_ROOT / "dbt"

# SCRAPE_SOURCE is the one knob not yet in Settings; keep the env read here.
SCRAPE_SOURCE = os.getenv("SCRAPE_SOURCE", "nhatot")
SCRAPE_TYPE = os.getenv("SCRAPE_TYPE", "all")
SCRAPE_LIMIT = int(os.getenv("SCRAPE_LIMIT", "100"))

# Postgres serving DB (the copy Metabase reads). Read from config.settings.
PG_HOST = settings.pg_host
PG_PORT = settings.pg_port
PG_DB = settings.pg_db
PG_USER = settings.pg_user
PG_PASSWORD = settings.pg_password
PG_SCHEMA = settings.pg_schema

# The dbt marts to publish to Postgres for Metabase to serve.
MARTS = [
    "listings",
    "price_by_district",
    "price_trend",
    "price_changes",
    "broker_listings",
    "price_per_sqm_by_ward",
    "listing_days_on_market",
    "listing_velocity",
    "broker_concentration",
]

# Marts that should never be empty after a successful run (an empty one means the pipeline
# broke upstream). price_changes/broker_listings can legitimately be empty, so they're excluded.
CRITICAL_NONEMPTY_MARTS = ["listings", "price_by_district"]

# dbt source tables (asset keys dagster-dbt derives for `source('raw', ...)`). The geocode
# step is what leaves these tables fully populated, so it produces these asset keys.
RAW_SOURCE_KEYS = [
    AssetKey(["raw", "raw_listings"]),
    AssetKey(["raw", "listing_price_history"]),
    AssetKey(["raw", "geocode_cache"]),
]

# Network/DB ops can hit transient failures; give them a couple of retries.
_NET_RETRY = RetryPolicy(max_retries=2, delay=10)

# dagster-dbt project. @dbt_assets reads the manifest at import time, so make sure one exists:
# prepare_if_dev() generates it under `dagster dev`; otherwise (CI / tests / a fresh container)
# fall back to a plain `dbt parse`, which writes target/manifest.json (CI also does this as an
# explicit step). dbt/ is bind-mounted into the Dagster containers, so this works there too.
dbt_project = DbtProject(project_dir=str(DBT_DIR), profiles_dir=str(DBT_DIR))
dbt_project.prepare_if_dev()
if not Path(dbt_project.manifest_path).exists():
    import subprocess

    subprocess.run(
        ["dbt", "parse", "--profiles-dir", str(DBT_DIR)], cwd=str(DBT_DIR), check=True
    )
dbt_resource = DbtCliResource(project_dir=dbt_project)


# --------------------------------------------------------------------------------------------
# Shared pipeline steps (plain functions) — called by both the assets and the op-based jobs so
# there is a single implementation of each step. `context` only needs a `.log`.
# --------------------------------------------------------------------------------------------
def _scalar_int(conn, sql: str, params=None) -> int:
    """Run a scalar `SELECT count(*)`-style query and return it as an int (0 if NULL/empty)."""
    row = conn.execute(sql, params or []).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _run_scrape_and_load(context) -> None:
    """Scrape listings and upsert them (records price history)."""
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


def _run_geocode(context) -> None:
    """Geocode any listings missing coordinates (cache → Nominatim → Goong → centroid)."""
    init_db()
    conn = get_connection()
    geocoder = Geocoder(conn)
    try:
        geocoder.geocode_pending_listings()
    finally:
        geocoder.close()
        conn.close()


def _run_check_mart_health(context) -> None:
    """Fail if a mart that must have rows came out empty (guard before publishing)."""
    conn = get_connection()
    try:
        counts: dict[str, int] = {}
        for mart in MARTS:
            present = _scalar_int(
                conn,
                "SELECT count(*) FROM duckdb_tables() "
                "WHERE database_name = current_database() AND table_name = ?",
                [mart],
            )
            if present:
                counts[mart] = _scalar_int(conn, f'SELECT count(*) FROM "{mart}"')
        context.log.info("Mart row counts: %s", counts)
        empty_critical = [m for m in CRITICAL_NONEMPTY_MARTS if counts.get(m, 0) == 0]
        if empty_critical:
            raise RuntimeError(
                f"Critical marts unexpectedly empty: {empty_critical} (counts={counts}). "
                "Refusing to publish empty marts to the Postgres serving DB."
            )
        # Snapshot the (healthy) counts so the drop-detection sensor can compare runs.
        record_mart_counts(conn, counts)
    finally:
        conn.close()


def _run_publish_to_postgres(context) -> None:
    """Publish the dbt marts to the Postgres serving DB atomically (staging + one swap txn).

    Each mart is written to a `<mart>__staging` table; then ALL marts are swapped into place in
    a single native Postgres transaction (DROP old + RENAME staging → final). Metabase always
    reads either the full previous marts or the full new ones — never an empty/half-published
    table.
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
            published = []
            swap_stmts = []
            for mart in MARTS:
                present = _scalar_int(
                    conn,
                    "SELECT count(*) FROM duckdb_tables() "
                    "WHERE database_name = current_database() AND table_name = ?",
                    [mart],
                )
                if not present:
                    context.log.info("Mart '%s' not found in DuckDB; skipping.", mart)
                    continue
                staging = f"{mart}__staging"
                conn.execute(f'DROP TABLE IF EXISTS pg.{PG_SCHEMA}."{staging}"')
                conn.execute(
                    f'CREATE TABLE pg.{PG_SCHEMA}."{staging}" AS SELECT * FROM "{mart}"'
                )
                n = _scalar_int(conn, f'SELECT count(*) FROM pg.{PG_SCHEMA}."{staging}"')
                published.append(f"{mart}={n}")
                swap_stmts.append(f'DROP TABLE IF EXISTS {PG_SCHEMA}."{mart}";')
                swap_stmts.append(f'ALTER TABLE {PG_SCHEMA}."{staging}" RENAME TO "{mart}";')

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


def _run_dbt_build_cli(context) -> None:
    """Run `dbt build` via DbtCliResource (used by the op-based jobs; no subprocess)."""
    invocation = DbtCliResource(project_dir=dbt_project).cli(["build"]).wait()
    if not invocation.is_successful():
        raise RuntimeError("dbt build failed (see dbt logs).")
    context.log.info("dbt build completed.")


# --------------------------------------------------------------------------------------------
# Asset graph for the daily refresh (dagster-dbt).
# --------------------------------------------------------------------------------------------
@asset(retry_policy=_NET_RETRY, compute_kind="python")
def scraped_listings(context: AssetExecutionContext) -> None:
    """Scrape the search results and upsert listings + price history into DuckDB."""
    _run_scrape_and_load(context)


@multi_asset(
    specs=[AssetSpec(key, deps=[AssetKey("scraped_listings")]) for key in RAW_SOURCE_KEYS],
    retry_policy=_NET_RETRY,
    compute_kind="python",
)
def geocoded_raw(context: AssetExecutionContext):
    """Geocode pending listings; leaves the raw_* source tables fully populated for dbt."""
    _run_geocode(context)
    for key in RAW_SOURCE_KEYS:
        yield MaterializeResult(asset_key=key)


@dbt_assets(manifest=dbt_project.manifest_path)
def dbt_models(context: AssetExecutionContext, dbt: DbtCliResource):
    """All dbt models + tests as assets (lineage + test results in the Dagster UI)."""
    yield from dbt.cli(["build"], context=context).stream()


@asset(
    deps=[AssetKey(mart) for mart in MARTS],
    retry_policy=_NET_RETRY,
    compute_kind="postgres",
)
def published_marts(context: AssetExecutionContext) -> None:
    """Health-check the marts, then publish them atomically to the Postgres serving DB."""
    _run_check_mart_health(context)
    _run_publish_to_postgres(context)


daily_refresh = define_asset_job(
    name="daily_refresh",
    selection=AssetSelection.all(),
    description="Full refresh: scrape → geocode → dbt models → publish to Postgres serving DB.",
)


# --------------------------------------------------------------------------------------------
# Op-based secondary jobs (weekly maintenance + schema drift check).
# --------------------------------------------------------------------------------------------
@op(out=Out(Nothing), retry_policy=_NET_RETRY)
def rescrape_active(context) -> None:
    """Re-check active nhatot listings: record price changes, mark vanished ones inactive."""
    init_db()
    client = SafeHTTPClient()
    conn = get_connection()
    try:
        result = rescrape_active_listings(conn, NhaTotScraper(client))
        context.log.info(
            "Rescrape: checked=%d price_updated=%d deactivated=%d",
            result.checked, result.price_updated, result.deactivated,
        )
    finally:
        conn.close()
        client.close()


@op(out=Out(Nothing), ins={"start": In(Nothing)}, retry_policy=_NET_RETRY)
def regeocode_low_confidence(context) -> None:
    """Re-attempt low-confidence (district-centroid) geocodes in case a better tier resolves."""
    init_db()
    conn = get_connection()
    geocoder = Geocoder(conn)
    try:
        upgraded = geocoder.regeocode_low_confidence()
        context.log.info("Re-geocode upgraded %d low-confidence addresses.", upgraded)
    finally:
        geocoder.close()
        conn.close()


@op(out=Out(Nothing), ins={"start": In(Nothing)})
def backup_database(context) -> None:
    """Snapshot the DuckDB working store (the irreplaceable raw/price-history data) + rotate."""
    backups_dir = os.getenv("BACKUPS_DIR", str(REPO_ROOT / "backups"))
    keep = int(os.getenv("BACKUP_KEEP", "7"))
    snapshot = backup_duckdb(settings.duckdb_path, backups_dir, keep=keep)
    context.log.info("DuckDB backup: %s", snapshot or "(skipped — no DB file)")


@op(out=Out(Nothing), ins={"start": In(Nothing)})
def run_dbt(context) -> None:
    """Rebuild the analytics marts via dbt (DbtCliResource)."""
    _run_dbt_build_cli(context)


@op(out=Out(Nothing), ins={"start": In(Nothing)})
def check_mart_health(context) -> None:
    """Guard rail before publishing: fail if a critical mart came out empty."""
    _run_check_mart_health(context)


@op(ins={"start": In(Nothing)}, retry_policy=_NET_RETRY)
def publish_to_postgres(context) -> None:
    """Publish the dbt marts to the Postgres serving DB atomically."""
    _run_publish_to_postgres(context)


@op(retry_policy=_NET_RETRY)
def check_api_schema(context) -> None:
    """Detect nhatot/chotot API schema drift against the NhaTotAd model (raises on drift)."""
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
def weekly_maintenance():
    """Rescrape (deactivate vanished) → upgrade geocodes → rebuild → publish, then back up."""
    rescraped = rescrape_active()
    transformed = run_dbt(start=regeocode_low_confidence(start=rescraped))
    published = check_mart_health(start=transformed)
    publish_to_postgres(start=published)
    backup_database(start=published)


@job
def schema_drift_check():
    """Standalone API-schema-drift check (fails loudly so the alert sensor fires)."""
    check_api_schema()


# --------------------------------------------------------------------------------------------
# Schedules + failure alerting.
# --------------------------------------------------------------------------------------------
daily_refresh_schedule = ScheduleDefinition(
    name="daily_refresh_schedule",
    job=daily_refresh,
    cron_schedule="0 3 * * *",
    execution_timezone="Asia/Ho_Chi_Minh",
    default_status=DefaultScheduleStatus.RUNNING,
)

schema_drift_schedule = ScheduleDefinition(
    name="schema_drift_schedule",
    job=schema_drift_check,
    cron_schedule="0 4 * * 1",  # weekly, Monday 04:00
    execution_timezone="Asia/Ho_Chi_Minh",
    default_status=DefaultScheduleStatus.RUNNING,
)

weekly_maintenance_schedule = ScheduleDefinition(
    name="weekly_maintenance_schedule",
    job=weekly_maintenance,
    cron_schedule="0 5 * * 0",  # weekly, Sunday 05:00
    execution_timezone="Asia/Ho_Chi_Minh",
    default_status=DefaultScheduleStatus.RUNNING,
)


@run_failure_sensor(default_status=DefaultSensorStatus.RUNNING)
def pipeline_failure_alert(context: RunFailureSensorContext) -> None:
    """Post a Slack alert when any job fails (no-op if SLACK_WEBHOOK_URL is unset)."""
    run = context.dagster_run
    error = context.failure_event.message or "(no error message)"
    post_slack(f":rotating_light: *{run.job_name}* failed (run {run.run_id[:8]}).\n{error}")


@run_status_sensor(
    run_status=DagsterRunStatus.SUCCESS,
    monitored_jobs=[daily_refresh, weekly_maintenance],
    default_status=DefaultSensorStatus.RUNNING,
)
def mart_drop_alert(context: RunStatusSensorContext) -> None:
    """After a successful refresh, alert if any mart shrank materially vs the previous run.

    The empty-mart guard only catches a mart going to zero; this catches a partial break that
    still produces some rows but far fewer than usual. No-op if SLACK_WEBHOOK_URL is unset.
    """
    conn = get_connection()
    try:
        drops = detect_row_drops(conn)
    finally:
        conn.close()
    if not drops:
        return
    lines = [
        f"• *{d['mart']}*: {d['previous']} → {d['current']} ({d['drop_pct']:.0%} drop)"
        for d in drops
    ]
    run = context.dagster_run
    context.log.warning("Mart row-count drops detected: %s", drops)
    post_slack(
        f":chart_with_downwards_trend: *{run.job_name}* mart row-count drop "
        f"(run {run.run_id[:8]}):\n" + "\n".join(lines)
    )


defs = Definitions(
    assets=[scraped_listings, geocoded_raw, dbt_models, published_marts],
    jobs=[daily_refresh, weekly_maintenance, schema_drift_check],
    schedules=[daily_refresh_schedule, weekly_maintenance_schedule, schema_drift_schedule],
    sensors=[pipeline_failure_alert, mart_drop_alert],
    resources={"dbt": dbt_resource},
)
