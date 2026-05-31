"""Integration test for `publish_marts_to_postgres` against a real Postgres (testcontainers).

Self-skips when Docker or `testcontainers` is unavailable (e.g. a laptop without Docker), so the
default `pytest` run stays hermetic; it exercises the real DuckDB→Postgres atomic-swap publish
when a Docker daemon is present.
"""
from __future__ import annotations

import logging

import pytest

testcontainers_pg = pytest.importorskip("testcontainers.postgres")
PostgresContainer = testcontainers_pg.PostgresContainer
# The publish logic lives in pipeline/publisher.py (dagster-free), so no dagster extra needed.

_LOG = logging.getLogger("test.publish")


@pytest.fixture(scope="module")
def pg_container():
    try:
        container = PostgresContainer(
            "postgres:16-alpine", username="danang", password="secret", dbname="danang"
        )
        container.start()
    except Exception as exc:  # Docker not running / image pull blocked → skip, don't fail.
        pytest.skip(f"Postgres testcontainer unavailable: {exc}")
    yield container
    container.stop()


def _seed_marts(duckdb_path, listings_rows: int, district_rows: int) -> None:
    import duckdb

    conn = duckdb.connect(str(duckdb_path))
    try:
        conn.execute("DROP TABLE IF EXISTS listings")
        conn.execute("DROP TABLE IF EXISTS price_by_district")
        conn.execute("CREATE TABLE listings (listing_id BIGINT, price BIGINT)")
        conn.execute(
            "INSERT INTO listings SELECT i, i * 1000 FROM range(?) t(i)", [listings_rows]
        )
        conn.execute("CREATE TABLE price_by_district (district VARCHAR, median_price BIGINT)")
        conn.execute(
            "INSERT INTO price_by_district SELECT 'D' || i, i * 5 FROM range(?) t(i)",
            [district_rows],
        )
    finally:
        conn.close()


def _pg_count(container, table: str) -> int:
    import psycopg2

    with psycopg2.connect(container.get_connection_url().replace("+psycopg2", "")) as pg:
        with pg.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM public.{table}")
            return cur.fetchone()[0]


def _pg_table_exists(container, table: str) -> bool:
    import psycopg2

    with psycopg2.connect(container.get_connection_url().replace("+psycopg2", "")) as pg:
        with pg.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", [f"public.{table}"])
            return cur.fetchone()[0] is not None


@pytest.fixture()
def publish(pg_container, tmp_path, monkeypatch):
    """Point publisher's PG_* + DuckDB path at the container/temp DB and return the module."""
    from danang_realestate.pipeline import publisher

    monkeypatch.setattr(publisher.settings, "duckdb_path", str(tmp_path / "marts.duckdb"))
    monkeypatch.setattr(publisher, "PG_HOST", pg_container.get_container_host_ip())
    monkeypatch.setattr(publisher, "PG_PORT", int(pg_container.get_exposed_port(5432)))
    monkeypatch.setattr(publisher, "PG_DB", "danang")
    monkeypatch.setattr(publisher, "PG_USER", "danang")
    monkeypatch.setattr(publisher, "PG_PASSWORD", "secret")
    monkeypatch.setattr(publisher, "PG_SCHEMA", "public")
    return publisher


def test_publish_creates_marts_in_postgres(publish, pg_container, tmp_path):
    _seed_marts(tmp_path / "marts.duckdb", listings_rows=10, district_rows=3)

    publish.publish_marts_to_postgres(_LOG)

    assert _pg_count(pg_container, "listings") == 10
    assert _pg_count(pg_container, "price_by_district") == 3
    # No staging leftovers after the atomic swap.
    assert not _pg_table_exists(pg_container, "listings__staging")


def test_republish_atomically_swaps_without_leftover_staging(publish, pg_container, tmp_path):
    _seed_marts(tmp_path / "marts.duckdb", listings_rows=10, district_rows=3)
    publish.publish_marts_to_postgres(_LOG)

    # A subsequent run with different row counts replaces the marts in place.
    _seed_marts(tmp_path / "marts.duckdb", listings_rows=4, district_rows=5)
    publish.publish_marts_to_postgres(_LOG)

    assert _pg_count(pg_container, "listings") == 4
    assert _pg_count(pg_container, "price_by_district") == 5
    assert not _pg_table_exists(pg_container, "listings__staging")
    assert not _pg_table_exists(pg_container, "price_by_district__staging")


def test_publish_requires_password(publish, monkeypatch, tmp_path):
    _seed_marts(tmp_path / "marts.duckdb", listings_rows=1, district_rows=1)
    monkeypatch.setattr(publish, "PG_PASSWORD", "")
    with pytest.raises(RuntimeError, match="password is not set"):
        publish.publish_marts_to_postgres(_LOG)
