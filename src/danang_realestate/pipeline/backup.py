"""Back up the DuckDB working store (the irreplaceable data).

raw_listings + listing_price_history + geocode_cache are the only data the pipeline can't
recreate (a re-scrape can't recover historical prices or vanished listings). The Postgres
serving DB, by contrast, is a regenerable copy of the dbt marts (`publish_to_postgres`), so it
needs no separate backup — restore it by re-running the pipeline.

`EXPORT DATABASE` writes a portable snapshot (schema.sql + Parquet) that `IMPORT DATABASE`
restores. Snapshots are timestamped and rotated.
"""
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import duckdb

logger = logging.getLogger(__name__)


def backup_duckdb(
    duckdb_path: str,
    backups_dir: str,
    keep: int = 7,
    now: Optional[datetime] = None,
) -> Optional[Path]:
    """Snapshot the DuckDB database to `backups_dir/duckdb-<UTC timestamp>/` and rotate.

    Returns the snapshot directory, or None if the source DB doesn't exist yet. Keeps the
    `keep` most recent snapshots, deleting older ones. Restore with DuckDB `IMPORT DATABASE`.
    """
    src = Path(duckdb_path)
    if not src.exists():
        logger.warning("DuckDB file %s does not exist; nothing to back up.", src)
        return None

    now = now or datetime.now(timezone.utc)
    dest_root = Path(backups_dir)
    dest_root.mkdir(parents=True, exist_ok=True)
    snapshot = dest_root / f"duckdb-{now:%Y%m%dT%H%M%SZ}"

    # Read-only connection so a concurrent reader can't be disrupted (the pipeline has
    # finished writing by the time the weekly backup runs).
    conn = duckdb.connect(str(src), read_only=True)
    try:
        # EXPORT DATABASE needs an empty/new target directory.
        conn.execute(f"EXPORT DATABASE '{snapshot}' (FORMAT PARQUET)")
    finally:
        conn.close()

    pruned = _rotate(dest_root, keep)
    logger.info(
        "Backed up DuckDB → %s (kept %d snapshots, pruned %d).",
        snapshot, keep, pruned,
    )
    return snapshot


def _rotate(dest_root: Path, keep: int) -> int:
    """Keep the `keep` most recent duckdb-* snapshot dirs; delete the rest. Returns # pruned."""
    snapshots = sorted(
        (p for p in dest_root.glob("duckdb-*") if p.is_dir()),
        key=lambda p: p.name,
    )
    to_prune = snapshots[:-keep] if keep > 0 else snapshots
    for old in to_prune:
        shutil.rmtree(old, ignore_errors=True)
    return len(to_prune)
