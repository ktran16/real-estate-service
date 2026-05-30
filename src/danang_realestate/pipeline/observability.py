"""Run observability: persist mart row counts per run and flag material drops.

The mart health check already counts rows before publishing. We additionally persist those
counts to a `mart_row_history` table (one row per mart per run) so a Dagster sensor can compare
the latest run against the previous one and alert when a mart shrinks materially — catching a
half-broken upstream that still produces *some* rows (so the empty-mart guard wouldn't fire)
but far fewer than usual.
"""
from __future__ import annotations

import logging

import duckdb

from danang_realestate.utils.timeutil import utcnow

logger = logging.getLogger(__name__)

# A mart shrinking by this fraction or more versus the previous run is treated as a drop worth
# alerting on (e.g. 0.30 = a ≥30% decline). Day-to-day churn is well below this.
DEFAULT_DROP_THRESHOLD = 0.30

_CREATE_SQL = """
    CREATE TABLE IF NOT EXISTS mart_row_history (
        run_at      TIMESTAMP NOT NULL,
        mart        VARCHAR   NOT NULL,
        row_count   BIGINT    NOT NULL
    )
"""


def ensure_history_table(conn: duckdb.DuckDBPyConnection) -> None:
    """Create the mart_row_history table if it doesn't already exist."""
    conn.execute(_CREATE_SQL)


def record_mart_counts(
    conn: duckdb.DuckDBPyConnection, counts: dict[str, int], run_at=None
) -> None:
    """Append a row-count snapshot for this run (one row per mart)."""
    if not counts:
        return
    ensure_history_table(conn)
    ts = run_at or utcnow()
    conn.executemany(
        "INSERT INTO mart_row_history (run_at, mart, row_count) VALUES (?, ?, ?)",
        [(ts, mart, int(n)) for mart, n in counts.items()],
    )


def detect_row_drops(
    conn: duckdb.DuckDBPyConnection, threshold: float = DEFAULT_DROP_THRESHOLD
) -> list[dict]:
    """Compare the two most recent snapshots; return marts that shrank by >= `threshold`.

    Each result is a dict ``{mart, previous, current, drop_pct}``. Returns ``[]`` when there is
    no prior snapshot to compare against (first run) or the table doesn't exist yet.
    """
    table = conn.execute(
        "SELECT count(*) FROM duckdb_tables() "
        "WHERE database_name = current_database() AND table_name = 'mart_row_history'"
    ).fetchone()
    if not table or not table[0]:
        return []

    runs = conn.execute(
        "SELECT DISTINCT run_at FROM mart_row_history ORDER BY run_at DESC LIMIT 2"
    ).fetchall()
    if len(runs) < 2:
        return []
    current_ts, prev_ts = runs[0][0], runs[1][0]

    current = dict(
        conn.execute(
            "SELECT mart, row_count FROM mart_row_history WHERE run_at = ?", [current_ts]
        ).fetchall()
    )
    previous = dict(
        conn.execute(
            "SELECT mart, row_count FROM mart_row_history WHERE run_at = ?", [prev_ts]
        ).fetchall()
    )

    drops: list[dict] = []
    for mart, prev_n in previous.items():
        if prev_n <= 0:
            continue
        cur_n = current.get(mart, 0)
        drop_pct = (prev_n - cur_n) / prev_n
        if drop_pct >= threshold:
            drops.append(
                {"mart": mart, "previous": prev_n, "current": cur_n, "drop_pct": drop_pct}
            )
    return drops
