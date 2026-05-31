"""Publish the dbt marts from DuckDB to the Postgres serving DB that Metabase reads.

This is the dagster-free home of the publish step so it can run from a plain host process
(`scripts/publish_to_pg.py`, `scripts/refresh.sh`) without importing dagster. The Dagster
orchestration imports `publish_marts_to_postgres` and wraps it in an op/asset.

Architecture: DuckDB is the pipeline's working store; a separate Postgres DB holds a serving
copy of the marts (Metabase's built-in Postgres driver, always-on). Because Metabase never
opens the DuckDB file there's no single-writer dance.
"""
from __future__ import annotations

import logging
from typing import Optional

from danang_realestate.config import settings
from danang_realestate.db import get_connection

logger = logging.getLogger(__name__)

# Postgres serving DB (the copy Metabase reads). Read from config.settings (single source).
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
    "deals",
]


def _scalar_int(conn, sql: str, params=None) -> int:
    """Run a scalar `SELECT count(*)`-style query and return it as an int (0 if NULL/empty)."""
    row = conn.execute(sql, params or []).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def publish_marts_to_postgres(log: Optional[logging.Logger] = None) -> None:
    """Publish the dbt marts to the Postgres serving DB atomically (staging + one swap txn).

    Each mart is written to a `<mart>__staging` table; then ALL marts are swapped into place in
    a single native Postgres transaction (DROP old + RENAME staging → final). Metabase always
    reads either the full previous marts or the full new ones — never an empty/half-published
    table.
    """
    log = log or logger
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
                    log.info("Mart '%s' not found in DuckDB; skipping.", mart)
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
        log.info(
            "Published marts to Postgres %s:%s (atomic swap) → %s",
            PG_HOST, PG_PORT, ", ".join(published) or "(none)",
        )
    finally:
        conn.close()
