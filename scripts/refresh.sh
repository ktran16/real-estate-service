#!/usr/bin/env bash
# Scheduled refresh of the Da Nang real estate marts.
#
# DuckDB is single-writer: while Metabase holds danang.duckdb open, the pipeline
# cannot write it. So this script stops Metabase, runs the pipeline, and restarts
# Metabase (only if it was running to begin with).
#
# Re-running run-all is the price-tracking loop: detect_price_changes compares each
# scraped price against the stored price and appends to listing_price_history, which
# feeds the price_changes mart. Run it on a schedule (see cron section in the README)
# to grow price history over time.
#
# Usage:
#   scripts/refresh.sh                 # run-all --type all --limit 100
#   SCRAPE_LIMIT=250 scripts/refresh.sh
#   WITH_RESCRAPE=1 scripts/refresh.sh # also recheck active listings (offline + price)
#
# Env overrides: SCRAPE_TYPE (all|sale|rent), SCRAPE_LIMIT (int), WITH_RESCRAPE (0|1).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

# cron runs with a minimal environment; make sure uv/docker are reachable.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

SCRAPE_TYPE="${SCRAPE_TYPE:-all}"
SCRAPE_LIMIT="${SCRAPE_LIMIT:-100}"
WITH_RESCRAPE="${WITH_RESCRAPE:-0}"

LOG_DIR="$REPO/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/refresh-$(date +%Y%m%d-%H%M%S).log"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

# Prevent overlapping runs (e.g. a slow run still going when cron fires again).
exec 9>"$REPO/.refresh.lock"
if ! flock -n 9; then
  log "Another refresh is already running; exiting."
  exit 0
fi

MB_DIR="$REPO/metabase"
RESTART_MB=0

restart_metabase() {
  if [ "$RESTART_MB" = "1" ]; then
    log "Restarting Metabase..."
    (cd "$MB_DIR" && docker compose start) >>"$LOG" 2>&1 \
      || log "WARN: failed to restart Metabase (start it manually: cd metabase && make start)"
  fi
}
trap restart_metabase EXIT

log "Refresh start (type=$SCRAPE_TYPE limit=$SCRAPE_LIMIT with_rescrape=$WITH_RESCRAPE)"

# Stop Metabase only if its container is currently running, to free the DuckDB file.
if [ -n "$(docker ps -q -f name=danang-metabase -f status=running 2>/dev/null)" ]; then
  log "Stopping Metabase to release the single-writer DuckDB file..."
  (cd "$MB_DIR" && docker compose stop) >>"$LOG" 2>&1
  RESTART_MB=1
else
  log "Metabase not running; leaving it stopped."
fi

log "Pipeline: run-all --type $SCRAPE_TYPE --limit $SCRAPE_LIMIT"
uv run danang-realestate run-all --type "$SCRAPE_TYPE" --limit "$SCRAPE_LIMIT" >>"$LOG" 2>&1

if [ "$WITH_RESCRAPE" = "1" ]; then
  # Recheck active listings for price changes + mark offline ones inactive, then
  # rebuild the marts so the change shows up.
  log "Rescrape: rechecking active listings (price changes + offline detection)..."
  uv run danang-realestate rescrape >>"$LOG" 2>&1
  log "Transform: rebuilding marts after rescrape..."
  uv run danang-realestate transform >>"$LOG" 2>&1
fi

log "Refresh complete. Log: $LOG"
