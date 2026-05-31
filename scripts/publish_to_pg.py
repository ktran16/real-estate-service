#!/usr/bin/env python3
"""Publish the dbt marts from DuckDB to the Postgres serving DB (atomic swap).

Standalone wrapper around orchestration._run_publish_to_postgres so a host-side
`run-all` (which only writes DuckDB) can push the refreshed marts to the Postgres
DB that Metabase reads. Uses the same PG_* settings (from .env) as the pipeline.
"""
import logging

from danang_realestate.orchestration import _run_publish_to_postgres

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


class _Ctx:
    """Minimal stand-in for the Dagster context (only .log is used)."""

    log = logging.getLogger("publish_to_pg")


if __name__ == "__main__":
    _run_publish_to_postgres(_Ctx())
