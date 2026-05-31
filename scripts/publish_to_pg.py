#!/usr/bin/env python3
"""Publish the dbt marts from DuckDB to the Postgres serving DB (atomic swap).

Standalone entrypoint so a host-side `run-all` (which only writes DuckDB) can push the
refreshed marts to the Postgres DB that Metabase reads. Uses the same PG_* settings (from
.env) as the pipeline. The publish logic lives in `pipeline.publisher` (dagster-free), so
this runs under a plain `uv run python` — no dagster extra required.
"""
import logging

from danang_realestate.pipeline.publisher import publish_marts_to_postgres

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


if __name__ == "__main__":
    publish_marts_to_postgres(logging.getLogger("publish_to_pg"))
