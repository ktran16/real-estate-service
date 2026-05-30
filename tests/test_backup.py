"""Tests for the DuckDB backup/rotation helper."""
from datetime import datetime, timezone

import duckdb

from danang_realestate.pipeline.backup import backup_duckdb


def _make_db(path):
    conn = duckdb.connect(str(path))
    conn.execute("CREATE TABLE raw_listings (listing_id BIGINT, price BIGINT)")
    conn.execute("INSERT INTO raw_listings VALUES (1, 100), (2, 200)")
    conn.close()


def test_backup_creates_restorable_snapshot(tmp_path):
    db = tmp_path / "danang.duckdb"
    _make_db(db)
    backups = tmp_path / "backups"

    snap = backup_duckdb(str(db), str(backups), keep=7)
    assert snap is not None and snap.exists()
    # EXPORT DATABASE writes a schema file + data; verify it round-trips via IMPORT.
    assert (snap / "schema.sql").exists()

    conn = duckdb.connect(":memory:")
    conn.execute(f"IMPORT DATABASE '{snap}'")
    assert conn.execute("SELECT count(*) FROM raw_listings").fetchone()[0] == 2
    conn.close()


def test_backup_missing_db_returns_none(tmp_path):
    assert backup_duckdb(str(tmp_path / "nope.duckdb"), str(tmp_path / "b")) is None


def test_backup_rotation_keeps_n(tmp_path):
    db = tmp_path / "danang.duckdb"
    _make_db(db)
    backups = tmp_path / "backups"

    # Three snapshots at distinct timestamps; keep=2 should leave the 2 most recent.
    for day in (1, 2, 3):
        backup_duckdb(
            str(db), str(backups), keep=2,
            now=datetime(2026, 5, day, 5, 0, 0, tzinfo=timezone.utc),
        )
    remaining = sorted(p.name for p in backups.glob("duckdb-*"))
    assert remaining == ["duckdb-20260502T050000Z", "duckdb-20260503T050000Z"]
